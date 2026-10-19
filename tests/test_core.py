"""基础烟雾测试：日期解析、待办四维视图、审批幂等。"""

from app.core.dates import parse_relative_due, week_range
from app.schemas import (
    ApprovalDecision,  # noqa: F401
    TodoCreate,
    TodoUpdate,
)
from app.services import mail as mail_svc
from app.services import todo as todo_svc


def test_parse_relative_due():
    d, needs = parse_relative_due("今天")
    assert d is not None and needs is False
    d2, needs2 = parse_relative_due("乱七八糟")
    assert d2 is None and needs2 is True


def test_todo_views_and_events():
    out = todo_svc.create_todo(
        TodoCreate(
            title="补完消融实验", source_quote="下周五前补完消融实验，结果给老师看一下"
        )
    )
    assert out.status.value == "todo"
    updated = todo_svc.update_todo(
        out.id, TodoUpdate(status="done", note="完成实验并发现效果下降")
    )
    assert updated is not None
    views = todo_svc.todo_views()
    assert "today" in views and "overdue" in views


def test_approval_version_and_idempotent_reject():
    app = mail_svc.prepare_email(
        user_id="dev",
        session_id="t1",
        to_email="teacher@example.com",
        subject="周报",
        body="本周进展……",
    )
    ver = app.payload_version
    # 改稿 → 版本应变
    app2 = mail_svc.prepare_email(
        user_id="dev",
        session_id="t1",
        to_email="teacher@example.com",
        subject="周报",
        body="本周进展……（已改）",
    )
    assert app2.payload_version != ver or str(app2.status) != "pending"

    r1 = mail_svc.decide_approval(
        user_id="dev",
        approval_id=app2.id,
        decision="rejected",
        payload_version_in=app2.payload_version,
    )
    assert r1["ok"] and r1["status"] == "rejected"
    r2 = mail_svc.decide_approval(
        user_id="dev",
        approval_id=app2.id,
        decision="approved",
        payload_version_in=app2.payload_version,
    )
    # 重复决策幂等，不执行发送
    assert r2.get("idempotent") or r2.get("executed") is False


def test_week_range():
    mon, sun = week_range()
    assert (sun - mon).days == 6


def test_deadline_sets_due_at():
    from app.core.dates import today
    from app.services.extract import extract_tasks_rule

    resp = extract_tasks_rule("下周五前补完消融实验，结果给老师看一下")
    t = resp.tasks[0]
    assert t.due_at is not None
    assert t.planned_date is None
    assert t.due_at.date() > today()


def test_search_tasks_marks_overdue():
    """search_tasks 自动标注已过期：逾期单列并带逾期天数，不混入进行中。"""
    from datetime import timedelta

    from app.agent.graph import search_tasks
    from app.core.dates import today
    from app.schemas import TodoCreate

    overdue_item = todo_svc.create_todo(
        TodoCreate(title="老曹交中期报告初稿", planned_date=today() - timedelta(days=2))
    )
    future_item = todo_svc.create_todo(
        TodoCreate(title="数据清洗完成", planned_date=today() + timedelta(days=4))
    )
    out = search_tasks.func("")
    assert "已过期" in out and "逾期 2 天" in out and "老曹交中期报告初稿" in out
    assert "数据清洗完成" in out
    # 逾期项不得同时出现在「进行中」段（按 id 判定各自只出现一次）
    assert out.count(str(overdue_item.id)) == 1
    assert out.count(str(future_item.id)) == 1
    # 按标题过滤
    filtered = search_tasks.func("中期报告")
    assert "中期报告" in filtered and "数据清洗" not in filtered


def test_extract_two_step_confirm_flow():
    """对话内纪要抽取两步流：先出候选不入库，确认后按 document_id 入库。"""
    from uuid import UUID

    from app.services.extract import extract_and_persist_note, persist_by_document

    note = "下周五前补完消融实验，结果给老师看一下。"
    # 第一步：仅候选，无 todo_id
    resp = extract_and_persist_note(note, user_id="dev", persist=False)
    assert resp.tasks and all(t.todo_id is None for t in resp.tasks)
    assert resp.document_id is not None

    # 第二步：确认入库，复用同一 document_id
    persisted = persist_by_document(resp.document_id, user_id="dev")
    assert persisted is not None
    assert persisted.document_id == resp.document_id
    assert all(t.todo_id is not None and str(UUID(str(t.todo_id))) for t in persisted.tasks)

    # 越权/不存在 → None
    assert persist_by_document(resp.document_id, user_id="someone-else") is None
    from app.services.extract import persist_by_document as pbd

    assert pbd(UUID(int=0), user_id="dev") is None


def test_session_mismatch_reject():
    app = mail_svc.prepare_email(
        user_id="u1",
        session_id="A",
        to_email="a@b.c",
        subject="s",
        body="body-v1",
    )
    r = mail_svc.decide_approval(
        user_id="u1",
        approval_id=app.id,
        decision="approved",
        payload_version_in=app.payload_version,
        session_id="B",
    )
    assert r.get("ok") is False and r.get("error") in ("session_mismatch", "forbidden")


def test_preferences_permanent_only():
    from app.services.preferences import is_permanent_request, set_preference

    assert is_permanent_request("以后周报都按三部分写")
    assert not is_permanent_request("这次写简短一点")
    r = set_preference("dev", "report_sections", "进展,问题,下周计划")
    assert r["ok"]
    r2 = set_preference("dev", "not_a_key", "x")
    assert not r2["ok"]


def test_report_edit_version():
    from app.services.report import edit_report, generate_weekly_report

    d = generate_weekly_report(user_id="ver")
    from app.services.report import get_report

    info = get_report(str(d.id), user_id="ver")
    assert info
    r_missing = edit_report(str(d.id), "x", user_id="ver", expected_hash=None)
    assert not r_missing["ok"] and r_missing["error"] == "content_version_required"
    r = edit_report(
        str(d.id),
        "# 新草稿\n改短",
        user_id="ver",
        expected_hash="bad",
    )
    assert not r["ok"] and r["error"] == "content_version_mismatch"
    r2 = edit_report(
        str(d.id),
        "# 新草稿\n改短",
        user_id="ver",
        expected_hash=info["content_hash"],
    )
    assert r2["ok"]
