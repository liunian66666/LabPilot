"""回归：批准只执行一次、artifacts 并发隔离、版本强制。"""

import threading

from app.agent.artifacts import add_artifact, begin_run, end_run, take_artifacts
from app.services import mail as mail_svc


def test_artifacts_via_tool_runner_concurrent():
    """模拟 ToolNode 工作线程：两个 run 并发执行工具，artifacts 各自非空且不串。"""
    from langchain_core.tools import tool

    from app.agent.tool_runner import execute_tool_calls, register_tools

    @tool
    def make_report() -> str:
        """生成周报产物。"""
        from app.agent.artifacts import add_artifact
        from app.services.report import generate_weekly_report

        d = generate_weekly_report(user_id="tool-run")
        add_artifact("report", str(d.id), title="周报草稿", status="draft")
        return f"report_id={d.id}"

    register_tools([make_report])
    calls = [{"name": "make_report", "args": {}, "id": "c1"}]

    results: dict[str, list] = {}

    def worker(run_id: str):
        msgs = execute_tool_calls(calls, run_id)
        # 新契约：execute_tool_calls 只返回消息，artifacts 留在 run 注册表，
        # 由调用方在 run 结束时统一取出（每轮只含本轮产物，不跨轮累积）。
        results[run_id] = take_artifacts(run_id)
        end_run(run_id)
        assert msgs and "report_id=" in msgs[0].content

    t1 = threading.Thread(target=worker, args=("runA",))
    t2 = threading.Thread(target=worker, args=("runB",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(results["runA"]) == 1
    assert len(results["runB"]) == 1
    assert results["runA"][0]["id"] != results["runB"][0]["id"]


def test_artifacts_per_run_isolated():
    """两请求并发写产物，各自只能取到自己的。"""
    results: dict[str, list] = {}

    def worker(run_id: str, ids: list[str]):
        begin_run(run_id)
        for i in ids:
            add_artifact("report", f"{run_id}-{i}", title=f"r-{i}")
        results[run_id] = take_artifacts(run_id)
        end_run(run_id)

    t1 = threading.Thread(target=worker, args=("runA", ["1", "2"]))
    t2 = threading.Thread(target=worker, args=("runB", ["1", "2", "3"]))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(results["runA"]) == 2
    assert len(results["runB"]) == 3
    assert all(a["id"].startswith("runA") for a in results["runA"])
    assert all(a["id"].startswith("runB") for a in results["runB"])


def test_artifacts_without_run_discarded():
    from app.agent import artifacts as art

    art._local.run_id = None
    art.add_artifact("report", "orphan")
    assert art.take_artifacts("nope") == []


def test_approve_executes_once():
    app = mail_svc.prepare_email(
        user_id="once",
        session_id="s-once",
        to_email="t@e.com",
        subject="s",
        body="body-once",
    )
    r1 = mail_svc.decide_approval(
        user_id="once",
        approval_id=app.id,
        decision="approved",
        payload_version_in=app.payload_version,
        session_id="s-once",
    )
    assert r1.get("executed") is True
    r2 = mail_svc.decide_approval(
        user_id="once",
        approval_id=app.id,
        decision="approved",
        payload_version_in=app.payload_version,
        session_id="s-once",
    )
    assert r2.get("executed") is not True


def test_concurrent_approve_only_one_executes():
    """并发批准同一审批单，执行次数只能为 1（原子领取）。"""
    app = mail_svc.prepare_email(
        user_id="conc",
        session_id="s-conc",
        to_email="t@e.com",
        subject="s",
        body="body-conc",
    )
    results: list[dict] = []
    lock = threading.Lock()

    def worker():
        r = mail_svc.decide_approval(
            user_id="conc",
            approval_id=app.id,
            decision="approved",
            payload_version_in=app.payload_version,
            session_id="s-conc",
        )
        with lock:
            results.append(r)

    ts = [threading.Thread(target=worker) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    executed = [r for r in results if r.get("executed") is True]
    assert len(executed) == 1, results


def test_find_approval_reuse():
    a1 = mail_svc.prepare_email(
        user_id="reuse",
        session_id="s-reuse",
        to_email="t@e.com",
        subject="s",
        body="body-reuse",
    )
    found = mail_svc.find_approval_by_version(
        user_id="reuse",
        session_id="s-reuse",
        payload_version=a1.payload_version,
    )
    assert found is not None
    assert str(found.id) == str(a1.id)


def test_force_new_allows_resend_after_reject():
    """拒绝后明确重发同内容：可新建审批单；旧单仍不可再执行。"""
    body = "同一封信内容"
    a1 = mail_svc.prepare_email(
        user_id="resend",
        session_id="s-resend",
        to_email="t@e.com",
        subject="s",
        body=body,
    )
    r1 = mail_svc.decide_approval(
        user_id="resend",
        approval_id=a1.id,
        decision="rejected",
        payload_version_in=a1.payload_version,
        session_id="s-resend",
    )
    assert r1.get("executed") is False
    a2 = mail_svc.prepare_email(
        user_id="resend",
        session_id="s-resend",
        to_email="t@e.com",
        subject="s",
        body=body,
        allow_duplicate=True,
    )
    assert str(a2.id) != str(a1.id)
    # 旧单再批不会执行
    r_old = mail_svc.decide_approval(
        user_id="resend",
        approval_id=a1.id,
        decision="approved",
        payload_version_in=a1.payload_version,
        session_id="s-resend",
    )
    assert r_old.get("executed") is not True
    # 新单批准执行一次
    r_new = mail_svc.decide_approval(
        user_id="resend",
        approval_id=a2.id,
        decision="approved",
        payload_version_in=a2.payload_version,
        session_id="s-resend",
    )
    assert r_new.get("executed") is True


def test_deadline_reminder_auto_after_opt_in():
    """开启授权后自动发送；同一 due_key 不重复；改期后可再发。"""
    from datetime import timedelta

    from app.core.dates import now
    from app.schemas import TodoCreate
    from app.services.reminders import (
        disable_reminder,
        enable_reminder,
        run_deadline_reminders,
    )
    from app.services.todo import create_todo, update_todo

    t = create_todo(
        TodoCreate(
            title="临期自动提醒",
            due_at=now() + timedelta(hours=1),
            priority="urgent_important",
            source="advisor",
        ),
        user_id="auto-rem",
    )
    # 未开启：跳过
    r0 = run_deadline_reminders("auto-rem")
    assert r0.get("skipped") == "disabled"
    # 开启授权
    en = enable_reminder("auto-rem", to_email="me@e.com", hours_before=24)
    assert en["ok"]
    r1 = run_deadline_reminders("auto-rem")
    assert r1.get("ok") and r1.get("sent", 0) >= 1
    # 重复跑不重发
    r2 = run_deadline_reminders("auto-rem")
    assert r2.get("sent", 0) == 0
    # 改期 → 新 due_key → 可再提醒
    update_todo(t.id, __import__("app.schemas", fromlist=["TodoUpdate"]).TodoUpdate(
        due_at=now() + timedelta(hours=2)
    ), user_id="auto-rem")
    r3 = run_deadline_reminders("auto-rem")
    assert r3.get("sent", 0) >= 1
    disable_reminder("auto-rem")


def test_reminder_concurrent_single_send():
    """并发扫描同一待办，mock 只应发送一次。"""
    import threading
    from datetime import timedelta

    from app.core.dates import now
    from app.schemas import TodoCreate
    from app.services.reminders import enable_reminder, run_deadline_reminders
    from app.services.todo import create_todo

    uid = "conc-rem"
    create_todo(
        TodoCreate(
            title="并发临期",
            due_at=now() + timedelta(hours=1),
            priority="urgent_important",
        ),
        user_id=uid,
    )
    enable_reminder(uid, to_email="c@e.com", hours_before=24)
    results: list[dict] = []
    lock = threading.Lock()

    def w():
        r = run_deadline_reminders(uid)
        with lock:
            results.append(r)

    ts = [threading.Thread(target=w) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    sent = sum(int(r.get("sent") or 0) for r in results)
    assert sent == 1, results


def test_reminder_failed_can_retry():
    """failed 记录允许重试领取；sent 不再领取。"""
    import uuid as _uuid
    from datetime import timedelta

    from sqlalchemy import text

    from app.core.dates import now
    from app.models.db import get_engine
    from app.schemas import TodoCreate
    from app.services.reminders import enable_reminder, run_deadline_reminders
    from app.services.todo import create_todo

    uid = "retry-rem"
    t = create_todo(
        TodoCreate(
            title="失败重试",
            due_at=now() + timedelta(hours=1),
            priority="urgent_important",
        ),
        user_id=uid,
    )
    enable_reminder(uid, to_email="r@e.com", hours_before=24)
    key = _uuid.UUID(str(t.id)).hex

    dk = f"due:{(now() + timedelta(hours=1)).replace(microsecond=0).isoformat()}"
    # 伪造 failed 记录
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO reminder_logs (id, user_id, todo_id, due_key, sent_at, status, detail) "
                "VALUES (:id, :u, :t, :d, :ts, 'failed', 'old')"
            ),
            {
                "id": _uuid.uuid4().hex,
                "u": uid,
                "t": key,
                "d": dk,
                "ts": now().isoformat(),
            },
        )
    # 该 due_key 需与 todos_need_reminder 一致——用 create 时的时间重算
    from app.services.reminders import todos_need_reminder

    items = todos_need_reminder(uid)
    real_key = next(i["due_key"] for i in items if i["id"] == str(t.id))
    if real_key != dk:
        with get_engine().begin() as conn:
            conn.execute(
                text(
                    "UPDATE reminder_logs SET due_key=:d WHERE user_id=:u AND todo_id=:t"
                ),
                {"d": real_key, "u": uid, "t": key},
            )
    r = run_deadline_reminders(uid)
    assert r.get("sent", 0) == 1
    r2 = run_deadline_reminders(uid)
    assert r2.get("sent", 0) == 0


def test_reminder_hours_window_uses_setting():
    from datetime import timedelta

    from app.core.dates import now
    from app.schemas import TodoCreate
    from app.services.reminders import enable_reminder, todos_need_reminder
    from app.services.todo import create_todo

    uid = "win-rem"
    create_todo(
        TodoCreate(title="36h后", due_at=now() + timedelta(hours=36)),
        user_id=uid,
    )
    # 默认 24h：不应包含
    assert not any(i["title"] == "36h后" for i in todos_need_reminder(uid, hours=24))
    enable_reminder(uid, to_email="w@e.com", hours_before=48)
    # 配置 48h：应包含（hours=None 读设置）
    assert any(i["title"] == "36h后" for i in todos_need_reminder(uid))


def test_reminder_log_migration_idempotent():
    """迁移幂等：已有唯一约束时重复跑安全。"""
    import uuid as _uuid

    from sqlalchemy import text

    from app.core.dates import now
    from app.models.db import get_engine, migrate_reminder_logs

    uid = "mig-rem"
    t = _uuid.uuid4().hex
    dk = "due:mig-test"
    ts = now().isoformat()
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO reminder_logs (id, user_id, todo_id, due_key, sent_at, status, detail) "
                "VALUES (:id, :u, :t, :d, :ts, 'sent', '')"
            ),
            {"id": _uuid.uuid4().hex, "u": uid, "t": t, "d": dk, "ts": ts},
        )
    migrate_reminder_logs(get_engine())
    migrate_reminder_logs(get_engine())
    with get_engine().begin() as conn:
        n = conn.execute(
            text(
                "SELECT COUNT(*) FROM reminder_logs WHERE user_id=:u AND todo_id=:t AND due_key=:d"
            ),
            {"u": uid, "t": t, "d": dk},
        ).scalar()
    assert n == 1


def test_classify_send_error_stages():
    import smtplib
    import socket

    from app.services.mail import classify_send_error

    e1 = smtplib.SMTPAuthenticationError(535, b"bad")
    e1.labpilot_send_stage = "auth"
    assert classify_send_error(e1) == "failed"

    e2 = smtplib.SMTPServerDisconnected("gone")
    e2.labpilot_send_stage = "send"
    assert classify_send_error(e2) == "unknown"

    e3 = ConnectionResetError("reset")
    e3.labpilot_send_stage = "send"
    assert classify_send_error(e3) == "unknown"

    e4 = socket.gaierror("dns")
    e4.labpilot_send_stage = "connect"
    assert classify_send_error(e4) == "failed"


def test_reminder_schema_not_ready_blocks_send():
    from app.models import db as dbmod
    from app.services.reminders import enable_reminder, run_deadline_reminders

    old = (dbmod._reminder_schema_ready, dbmod._reminder_schema_error)
    try:
        dbmod._reminder_schema_ready = False
        dbmod._reminder_schema_error = "index missing"
        enable_reminder("schema-block", to_email="s@e.com")
        r = run_deadline_reminders("schema-block")
        assert r.get("skipped") == "schema_not_ready"
        assert r.get("sent") == 0
    finally:
        dbmod._reminder_schema_ready, dbmod._reminder_schema_error = old


def test_edit_report_requires_hash():
    from app.services.report import edit_report, generate_weekly_report

    d = generate_weekly_report(user_id="hash-req")
    r = edit_report(str(d.id), "x", user_id="hash-req", expected_hash=None)
    assert not r["ok"]
    assert r["error"] == "content_version_required"
