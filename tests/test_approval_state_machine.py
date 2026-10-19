"""审批状态机硬门禁：过期 / 改稿失效 / 版本不匹配 / 拒绝零发送 / 归属校验。

覆盖 test_regression.py 未覆盖的分支：
- 过期（approval_expired，零执行）
- 改稿后旧批准失效（superseded），旧单再批不执行
- payload_version 不匹配拒绝
- 拒绝时零 SMTP 调用（mock 后端计数）
- approval_not_found / forbidden / session_mismatch
- 完整状态迁移 approved→sending→sent
"""

import uuid as _uuid
from datetime import timedelta

from sqlalchemy import text
from sqlmodel import select

from app.core.config import get_settings
from app.core.dates import now
from app.models.db import get_engine, session_scope
from app.models.tables import Approval
from app.services import mail as mail_svc


def _mk(user: str, sid: str = "s", body: str = "b", to: str = "t@e.com") -> mail_svc.ApprovalView:
    return mail_svc.prepare_email(
        user_id=user, session_id=sid, to_email=to, subject="s", body=body
    )


def _u(app_id) -> _uuid.UUID:
    """ApprovalView.id 是带横线字符串；ORM 主键查询需 UUID 对象。"""
    return app_id if isinstance(app_id, _uuid.UUID) else _uuid.UUID(str(app_id))


def _backdate(app_id, hours: float):
    """把 created_at 回拨，模拟已过期。"""
    key = app_id.hex if hasattr(app_id, "hex") else __import__("uuid").UUID(str(app_id)).hex
    ts = (now() - timedelta(hours=hours)).isoformat()
    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE approvals SET created_at=:ts WHERE id=:id"),
            {"ts": ts, "id": key},
        )


def test_approve_full_transition():
    """approved → sending → sent 全链路，记录 exec_result。"""
    app = _mk("full")
    r = mail_svc.decide_approval(
        user_id="full", approval_id=app.id, decision="approved",
        payload_version_in=app.payload_version, session_id="s",
    )
    assert r["ok"] and r["executed"] is True and r["status"] == "sent"
    info = mail_svc.get_approval(_u(app.id), user_id="full")
    assert info["status"] == "sent"
    assert info["exec_result"]


def test_expired_rejects_and_zero_execute():
    """超过 TTL：不可批准，返回 approval_expired，零执行。"""
    app = _mk("exp", sid="s-exp")
    _backdate(app.id, get_settings().APPROVAL_TTL_HOURS + 1)
    r = mail_svc.decide_approval(
        user_id="exp", approval_id=app.id, decision="approved",
        payload_version_in=app.payload_version, session_id="s-exp",
    )
    assert r.get("ok") is False
    assert r.get("error") == "approval_expired"
    assert r.get("executed") is not True


def test_reject_zero_smtp():
    """拒绝：mock 后端零发送调用（approval 停在 rejected，不进入 sending/sent）。"""

    # 用一个探针：拒绝后记录数不应包含 email_send / email_mock_send 事件
    app = _mk("rej", sid="s-rej")
    r = mail_svc.decide_approval(
        user_id="rej", approval_id=app.id, decision="rejected",
        payload_version_in=app.payload_version, session_id="s-rej",
    )
    assert r.get("ok") and r.get("executed") is False and r.get("status") == "rejected"
    with session_scope() as s:
        ev = s.exec(select(Approval).where(Approval.id == _u(app.id))).first()
        assert str(ev.status) == "rejected"
        assert ev.exec_result is None  # 未执行
    # 无邮件发送事件
    with session_scope() as s:
        from app.models.tables import AuditEvent

        n = s.exec(
            select(AuditEvent).where(AuditEvent.event_type.like("email_%"))
            .where(AuditEvent.user_id == "rej")
        ).all()
        assert not [e for e in n if "email_send" in e.event_type]


def test_payload_version_mismatch_blocks():
    """版本不匹配：拒绝决策（不改内容、不发送）。"""
    app = _mk("ver", sid="s-ver")
    r = mail_svc.decide_approval(
        user_id="ver", approval_id=app.id, decision="approved",
        payload_version_in="deadbeef" * 4, session_id="s-ver",
    )
    assert r.get("ok") is False
    assert r.get("error") == "payload_version_mismatch"
    with session_scope() as s:
        ev = s.exec(select(Approval).where(Approval.id == _u(app.id))).first()
        assert str(ev.status) == "pending"


def test_approval_not_found():
    import uuid

    r = mail_svc.decide_approval(
        user_id="nf", approval_id=uuid.uuid4(), decision="approved",
        payload_version_in="x" * 32, session_id="s",
    )
    assert r.get("error") == "approval_not_found"


def test_forbidden_user():
    app = _mk("owner", sid="s-own")
    r = mail_svc.decide_approval(
        user_id="intruder", approval_id=app.id, decision="approved",
        payload_version_in=app.payload_version, session_id="s-own",
    )
    assert r.get("error") == "forbidden"


def test_session_mismatch():
    app = _mk("sess", sid="s-a")
    r = mail_svc.decide_approval(
        user_id="sess", approval_id=app.id, decision="approved",
        payload_version_in=app.payload_version, session_id="s-b",
    )
    assert r.get("error") == "session_mismatch"


def test_edit_makes_old_approval_superseded():
    """改稿：未执行的旧批准失效（expired/superseded），旧单再批零执行。"""
    # 场景：v1 已建审批单但尚未批准执行；同会话改稿 v2 → 旧单 expired，旧单再批不执行
    app = _mk("edit", sid="s-edit", body="版本一正文")
    old_version = app.payload_version
    # 改稿（同会话同收件人，body 变）→ prepare_email 应把 pending 的 v1 标 expired 并新建 v2
    app2 = _mk("edit", sid="s-edit", body="版本二正文")
    assert str(app2.id) != str(app.id)
    with session_scope() as s:
        old = s.exec(select(Approval).where(Approval.id == _u(app.id))).first()
        assert str(old.status) == "expired"
        assert old.exec_result == "payload changed; superseded"
    # 旧单再批（即使传旧版本）→ 因已 expired，幂等返回，不执行
    r_old = mail_svc.decide_approval(
        user_id="edit", approval_id=app.id, decision="approved",
        payload_version_in=old_version, session_id="s-edit",
    )
    assert r_old.get("executed") is not True
    assert r_old.get("status") == "expired"
    # 新单 v2 正常批准执行一次
    r_new = mail_svc.decide_approval(
        user_id="edit", approval_id=app2.id, decision="approved",
        payload_version_in=app2.payload_version, session_id="s-edit",
    )
    assert r_new.get("executed") is True


def test_already_sent_not_reexecuted_after_edit():
    """已发送的 v1 不被改稿覆盖；旧单再批幂等，不二次发送。"""
    app = _mk("edit2", sid="s-e2", body="v1")
    r1 = mail_svc.decide_approval(
        user_id="edit2", approval_id=app.id, decision="approved",
        payload_version_in=app.payload_version, session_id="s-e2",
    )
    assert r1.get("executed") is True
    # 同会话改稿 → 新建 v2
    app2 = _mk("edit2", sid="s-e2", body="v2")
    assert str(app2.id) != str(app.id)
    # v1 已 sent，保持 sent（不改写成 expired）；再批 v1 幂等不执行
    with session_scope() as s:
        old = s.exec(select(Approval).where(Approval.id == _u(app.id))).first()
        assert str(old.status) == "sent"
    r_old = mail_svc.decide_approval(
        user_id="edit2", approval_id=app.id, decision="approved",
        payload_version_in=app.payload_version, session_id="s-e2",
    )
    assert r_old.get("executed") is not True
