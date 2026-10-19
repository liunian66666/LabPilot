"""邮件审批与受控执行器。

- prepare：建/更新审批单（payload_version 内容哈希）；改稿使旧批准失效
- decide：原子 pending → approved|rejected；版本必须匹配；重复决策幂等
- execute：仅 approved + version 匹配；sending → sent|failed|unknown；unknown 不自动重发
"""

import hashlib
import json
from typing import Any
from uuid import UUID

from sqlmodel import select

from app.core.config import get_settings
from app.core.dates import now
from app.core.logging import log_event, log_exception
from app.models.db import session_scope
from app.models.tables import Approval, ApprovalStatus
from app.services.audit import audit

_PENDING = ApprovalStatus.PENDING.value
_APPROVED = ApprovalStatus.APPROVED.value
_REJECTED = ApprovalStatus.REJECTED.value


def payload_version(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


class ApprovalView(dict):
    """轻量返回值：避免 ORM 对象脱离 session 后无法访问。"""

    @property
    def id(self):
        return self["id"]

    @property
    def payload_version(self):
        return self["payload_version"]

    @property
    def status(self):
        return self["status"]

    @property
    def payload(self):
        return self["payload"]


def _view(app: Approval) -> ApprovalView:
    return ApprovalView(
        id=str(app.id),
        user_id=app.user_id,
        session_id=app.session_id,
        payload=app.payload,
        payload_version=app.payload_version,
        status=str(app.status),
    )


def prepare_email(
    *,
    user_id: str,
    session_id: str,
    to_email: str,
    subject: str,
    body: str,
    to_name: str = "",
    allow_duplicate: bool = False,
) -> ApprovalView:
    payload = {
        "to_email": to_email,
        "to_name": to_name,
        "subject": subject,
        "body": body,
    }
    version = payload_version(payload)
    from uuid import uuid4

    from sqlalchemy import update
    from sqlalchemy.exc import IntegrityError

    from app.core.mail_intent import intent_key
    from app.models.db import reminder_schema_status

    if not reminder_schema_status()[0]:
        raise RuntimeError("Mail schema is not ready; sending is disabled")
    key = intent_key(user_id, session_id, version)
    try:
        with session_scope() as session:
            existing = session.exec(
                select(Approval).where(Approval.intent_key == key)
            ).first()
            if existing:
                if not allow_duplicate:
                    return _view(existing)
                key = uuid4().hex
            # 内容变化创建新记录，只失效旧的 pending，绝不改写 approved/sending/sent。
            session.exec(
                update(Approval)
                .where(
                    Approval.user_id == user_id,
                    Approval.session_id == session_id,
                    Approval.status == _PENDING,
                    Approval.payload_version != version,
                )
                .values(
                    status=ApprovalStatus.EXPIRED.value,
                    exec_result="payload changed; superseded",
                )
            )
            approval = Approval(
                user_id=user_id,
                session_id=session_id,
                action_type="send_email",
                payload=payload,
                payload_version=version,
                intent_key=key,
                status=_PENDING,
            )
            session.add(approval)
            session.flush()
            result = _view(approval)
    except IntegrityError:
        # 并发准备同一内容，唯一键赢家已建立该单，返回它而不是再创建。
        with session_scope() as session:
            existing = session.exec(
                select(Approval).where(Approval.intent_key == key)
            ).first()
            if not existing:
                raise
            return _view(existing)
    audit(
        user_id, "approval_proposed", session_id, approval_id=result.id, version=version
    )
    log_event(
        "approval_ready", approval_id=result.id, version=version, status=result.status
    )
    return result


def _cas_status(
    approval_id: UUID | str,
    from_status: str,
    to_status: str,
    *,
    version: str | None = None,
    user_id: str | None = None,
) -> bool:
    """单条 UPDATE 条件迁移；仅 rowcount=1 的调用方获得执行资格。"""
    from sqlalchemy import text

    from app.models.db import get_engine

    # SQLite 存 UUID 多为 32 位 hex，与 str(uuid) 带杠不一致
    key = (
        approval_id.hex if isinstance(approval_id, UUID) else UUID(str(approval_id)).hex
    )
    ts = now().isoformat()
    conditions = ""
    if version is not None:
        conditions += " AND payload_version=:version"
    if user_id is not None:
        conditions += " AND user_id=:user_id"
    with get_engine().begin() as conn:
        r = conn.execute(
            text(
                "UPDATE approvals SET status=:to_s, updated_at=:ts "
                "WHERE id=:id AND status=:from_s" + conditions
            ),
            {
                "to_s": to_status,
                "ts": ts,
                "id": key,
                "from_s": from_status,
                "version": version,
                "user_id": user_id,
            },
        )
        return int(getattr(r, "rowcount", 0) or 0) == 1


def decide_approval(
    *,
    user_id: str,
    approval_id: UUID,
    decision: str,
    payload_version_in: str,
    session_id: str = "",
) -> dict[str, Any]:
    from app.models.db import reminder_schema_status

    if not reminder_schema_status()[0]:
        return {"ok": False, "error": "schema_not_ready"}
    if decision not in ("approved", "rejected"):
        return {"ok": False, "error": "invalid_decision"}
    outcome: dict[str, Any] = {}
    audit_event: tuple[str, dict[str, Any]] | None = None
    if not isinstance(approval_id, UUID):
        approval_id = UUID(str(approval_id))

    with session_scope() as s:
        app = s.get(Approval, approval_id)
        if not app:
            return {"ok": False, "error": "approval_not_found"}
        if app.user_id != user_id:
            return {"ok": False, "error": "forbidden"}
        if session_id and app.session_id and app.session_id != session_id:
            return {"ok": False, "error": "session_mismatch"}
        if app.payload_version != payload_version_in:
            return {"ok": False, "error": "payload_version_mismatch"}
        from datetime import timedelta

        ttl = get_settings().APPROVAL_TTL_HOURS
        if app.created_at and (now() - app.created_at) > timedelta(hours=ttl):
            _cas_status(approval_id, _PENDING, ApprovalStatus.EXPIRED.value)
            return {"ok": False, "error": "approval_expired"}

        str_status = str(app.status)
        payload_snapshot = dict(app.payload or {})
        if str_status != _PENDING:
            return {
                "ok": True,
                "id": str(app.id),
                "status": str_status,
                "idempotent": True,
                "exec_result": app.exec_result,
                "executed": False,
            }

    # 原子决策：pending → approved|rejected（只有一路成功）
    target = _APPROVED if decision == "approved" else _REJECTED
    won = _cas_status(
        approval_id, _PENDING, target, version=payload_version_in, user_id=user_id
    )
    if not won:
        with session_scope() as s:
            app = s.get(Approval, approval_id)
            return {
                "ok": True,
                "id": str(approval_id),
                "status": str(app.status) if app else "unknown",
                "idempotent": True,
                "executed": False,
            }

    audit_event = (
        "approval_approved" if decision == "approved" else "approval_rejected",
        {"approval_id": str(approval_id), "version": payload_version_in},
    )
    log_event(
        "approval_decided",
        approval_id=str(approval_id),
        decision=decision,
        user_id=user_id,
        session_id=session_id or "dev",
    )

    if decision != "approved":
        outcome = {
            "ok": True,
            "id": str(approval_id),
            "status": _REJECTED,
            "executed": False,
        }
    else:
        # 原子领取 sending：并发批准仅一个走到 SMTP
        claimed = _cas_status(
            approval_id,
            _APPROVED,
            ApprovalStatus.SENDING.value,
            version=payload_version_in,
            user_id=user_id,
        )
        if not claimed:
            outcome = {
                "ok": True,
                "id": str(approval_id),
                "status": "conflict",
                "executed": False,
                "error": "not_claimable",
            }
        else:
            settings = get_settings()
            backend = settings.MAIL_BACKEND
            try:
                if backend == "smtp":
                    result = _send_smtp(payload_snapshot)
                else:
                    result = {
                        "mode": "mock",
                        "message": "email accepted by mock executor",
                    }
                    log_event(
                        "email_mock_send",
                        approval_id=str(approval_id),
                        to=payload_snapshot.get("to_email"),
                    )
                with session_scope() as s:
                    app = s.get(Approval, approval_id)
                    if app:
                        app.status = ApprovalStatus.SENT.value
                        app.exec_result = json.dumps(result, ensure_ascii=False)
                        app.exec_attempted_at = now()
                outcome = {
                    "ok": True,
                    "id": str(approval_id),
                    "status": "sent",
                    "executed": True,
                    "result": result,
                }
                log_event(
                    "email_send_success",
                    approval_id=str(approval_id),
                    backend=backend,
                )
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                end_status = classify_send_error(
                    e, getattr(e, "labpilot_send_stage", None)
                )
                log_event(
                    "email_send_failed",
                    approval_id=str(approval_id),
                    end_status=end_status,
                    error=msg,
                )
                log_exception("email_send_exception", approval_id=str(approval_id))
                with session_scope() as s:
                    app = s.get(Approval, approval_id)
                    if app:
                        app.status = end_status
                        app.exec_result = msg
                outcome = {
                    "ok": False,
                    "id": str(approval_id),
                    "status": end_status,
                    "executed": True,
                    "error": msg,
                    "auto_retry": False,
                }

    if audit_event:
        audit(user_id, audit_event[0], session_id or "dev", **audit_event[1])
    return outcome


def classify_send_error(exc: BaseException, stage: str | None = None) -> str:
    """发送失败分类：连接/认证前=failed；发送阶段或结果不明=unknown。"""
    import smtplib
    import socket

    st = stage or getattr(exc, "labpilot_send_stage", None) or ""
    # 仅连接/认证失败：邮件未被接收
    if st in ("connect", "auth", "starttls"):
        return "failed"
    if isinstance(
        exc,
        (smtplib.SMTPConnectError, smtplib.SMTPAuthenticationError, socket.gaierror),
    ):
        return "failed"
    # 发送中/之后断开、超时、无法确认
    if st == "send" or isinstance(
        exc,
        (
            smtplib.SMTPServerDisconnected,
            ConnectionResetError,
            TimeoutError,
            socket.timeout,
            OSError,
        ),
    ):
        return "unknown"
    return "unknown"


def _execute_locked(s, app: Approval) -> dict[str, Any]:
    """兼容保留：实际领取走 _cas_status。"""
    return {
        "ok": True,
        "id": str(app.id),
        "status": str(app.status),
        "executed": False,
    }


def _smtp_local_hostname() -> str:
    """SMTP EHLO/HELO 用的本机名。

    优先级：EMAIL_SMTP_LOCAL_HOSTNAME 显式配置 > 本机 FQDN。
    Windows 中文计算机名（如「曹浩冉.lan」）会让 smtplib 的 ASCII 命令编码
    直接崩溃，非 ASCII 时回退 localhost（Docker 容器 ID 天然 ASCII，不受影响）。
    """
    import socket

    explicit = (get_settings().EMAIL_SMTP_LOCAL_HOSTNAME or "").strip()
    if explicit:
        return explicit
    try:
        name = socket.getfqdn()
        name.encode("ascii")
        return name
    except (UnicodeEncodeError, OSError):
        return "localhost"


def _send_smtp(payload: dict[str, Any]) -> dict[str, Any]:
    """SMTP 发送；异常带 stage，便于区分 failed / unknown。"""
    import smtplib
    import ssl
    from email.message import EmailMessage

    cfg = get_settings()
    msg = EmailMessage()
    msg.set_content(payload.get("body", ""))
    msg["Subject"] = payload.get("subject", "")
    msg["From"] = f"{cfg.EMAIL_FROM_NAME} <{cfg.EMAIL_SMTP_USER}>"
    msg["To"] = payload.get("to_email", "")

    hostname = _smtp_local_hostname()
    stage = "connect"
    try:
        if cfg.EMAIL_SMTP_USE_SSL:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(
                cfg.EMAIL_SMTP_HOST,
                cfg.EMAIL_SMTP_PORT,
                context=context,
                timeout=30,
                local_hostname=hostname,
            ) as server:
                stage = "auth"
                server.login(cfg.EMAIL_SMTP_USER, cfg.EMAIL_SMTP_PASSWORD)
                stage = "send"
                server.sendmail(
                    cfg.EMAIL_SMTP_USER, [payload.get("to_email", "")], msg.as_string()
                )
        else:
            with smtplib.SMTP(
                cfg.EMAIL_SMTP_HOST,
                cfg.EMAIL_SMTP_PORT,
                timeout=30,
                local_hostname=hostname,
            ) as server:
                stage = "starttls"
                server.ehlo()
                server.starttls()
                stage = "auth"
                server.login(cfg.EMAIL_SMTP_USER, cfg.EMAIL_SMTP_PASSWORD)
                stage = "send"
                server.sendmail(
                    cfg.EMAIL_SMTP_USER, [payload.get("to_email", "")], msg.as_string()
                )
        return {"mode": "smtp", "to": payload.get("to_email")}
    except Exception as e:
        # 附带阶段，上层据此判定 failed vs unknown
        e.labpilot_send_stage = stage  # type: ignore[attr-defined]
        raise


def find_approval_by_version(
    *, user_id: str, session_id: str, payload_version: str
) -> ApprovalView | None:
    """按会话+内容版本查找已有审批单（中断重入幂等）。"""
    with session_scope() as s:
        row = s.exec(
            select(Approval)
            .where(Approval.user_id == user_id)
            .where(Approval.session_id == session_id)
            .where(Approval.payload_version == payload_version)
            .where(Approval.action_type == "send_email")
            .order_by(Approval.created_at.desc())
        ).first()
        return _view(row) if row else None


def get_approval(approval_id: UUID, user_id: str = "dev") -> dict[str, Any] | None:
    with session_scope() as s:
        app = s.get(Approval, approval_id)
        if not app or app.user_id != user_id:
            return None
        return {
            "id": str(app.id),
            "status": str(app.status),
            "payload": app.payload,
            "payload_version": app.payload_version,
            "exec_result": app.exec_result,
            "session_id": app.session_id,
            "user_id": app.user_id,
        }
