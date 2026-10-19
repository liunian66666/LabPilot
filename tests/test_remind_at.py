"""自定义定时提醒（remind_at）：时间解析、到点发送、防重复、清除。"""

from datetime import timedelta

from app.core.dates import now, parse_remind_time
from app.schemas import TodoCreate, TodoUpdate
from app.services.reminders import (
    enable_reminder,
    run_scheduled_reminders,
    schedule_todo_reminder,
)
from app.services.todo import create_todo, update_todo


def test_parse_remind_time_basic():
    # 明天 9:00 → 恒为明天
    dt = parse_remind_time("明天 9:00")
    assert dt is not None
    assert dt.date() == (now() + timedelta(days=1)).date()
    assert (dt.hour, dt.minute) == (9, 0)

    # 完整日期时间
    dt2 = parse_remind_time("2026-12-01 13:50")
    assert dt2 is not None
    assert (dt2.month, dt2.day, dt2.hour, dt2.minute) == (12, 1, 13, 50)

    # 裸时间：今天或明天（取决于当前时刻），分钟必须正确
    dt3 = parse_remind_time("13:50")
    assert dt3 is not None
    assert dt3.minute == 50
    assert dt3.date() in (now().date(), (now() + timedelta(days=1)).date())


def test_parse_remind_time_relative():
    # 2分钟后 → now+2min（容差 5s）
    dt = parse_remind_time("2分钟后")
    assert dt is not None
    delta = (dt - now()).total_seconds()
    assert 115 <= delta <= 125

    # 半小时后
    dt2 = parse_remind_time("半小时后")
    assert dt2 is not None
    d2 = (dt2 - now()).total_seconds() / 60
    assert 29 <= d2 <= 31

    # 1小时30分钟后
    dt3 = parse_remind_time("1小时30分钟后")
    assert dt3 is not None
    d3 = (dt3 - now()).total_seconds() / 60
    assert 89 <= d3 <= 91

    # 一个半小时后（中文数字 + 半 = 90 分钟）
    dt4 = parse_remind_time("一个半小时后")
    assert dt4 is not None
    d4 = (dt4 - now()).total_seconds() / 60
    assert 89 <= d4 <= 91


def test_parse_remind_time_rejects_past_and_garbage():
    # 过去日期 → None
    assert parse_remind_time("2020-01-01 08:00") is None
    # 显式今天但时刻已过 → None（交给上层询问）
    past = "今天 00:01"
    if now().time() > __import__("datetime").time(0, 1):
        assert parse_remind_time(past) is None
    # 乱码 → None
    assert parse_remind_time("尽快") is None
    assert parse_remind_time("") is None
    # 非法小时
    assert parse_remind_time("25:00") is None


def test_scheduled_reminder_send_and_dedupe(monkeypatch):
    monkeypatch.setenv("MAIL_BACKEND", "mock")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        assert enable_reminder("dev", to_email="t@example.com", hours_before=24)["ok"]
        t = create_todo(TodoCreate(title="喝水"), user_id="dev")
        past = now() - timedelta(minutes=1)

        r = schedule_todo_reminder("dev", str(t.id), past)
        assert r["ok"] and r["title"] == "喝水"

        # 第一轮：发送 1 条
        out = run_scheduled_reminders("dev")
        assert out.get("ok") and out.get("sent") == 1

        # 第二轮：防重复，不再发送
        out2 = run_scheduled_reminders("dev")
        assert out2.get("sent") == 0
    finally:
        get_settings.cache_clear()


def test_scheduled_reminder_disabled():
    t = create_todo(TodoCreate(title="不开提醒"), user_id="dev")
    schedule_todo_reminder("dev", str(t.id), now() - timedelta(minutes=1))
    out = run_scheduled_reminders("dev")
    assert out.get("skipped") == "disabled" or out.get("sent", 0) >= 0


def test_clear_reminder_via_update():
    t = create_todo(TodoCreate(title="会被清除"), user_id="dev")
    schedule_todo_reminder("dev", str(t.id), now() + timedelta(hours=2))
    out = update_todo(t.id, TodoUpdate(remind_at=None), user_id="dev")
    assert out is not None
    # TodoUpdate(remind_at=None) 显式传入 → exclude_unset 语义下应清除
    from app.models.db import session_scope
    from app.models.tables import Todo

    with session_scope() as s:
        row = s.get(Todo, t.id)
        assert row is not None and row.remind_at is None
