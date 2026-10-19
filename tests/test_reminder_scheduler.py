"""验证后台实际调度入口，多用户数据库与发送服务联动；不连接真实 SMTP。"""

import asyncio
from datetime import timedelta

import pytest
from sqlmodel import create_engine, select

from app.core.dates import now
from app.models import db
from app.models.tables import ReminderLog, ReminderSetting, Todo
from app.services import reminder_scheduler as scheduler
from app.services import reminders


@pytest.fixture
def reminder_db(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'reminders.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(db, "_engine", engine)
    monkeypatch.setattr(db, "_reminder_schema_ready", False)
    monkeypatch.setattr(db, "_reminder_schema_error", None)
    db.init_db()
    yield
    engine.dispose()


def add_user(user_id, *, enabled=True, configured=True):
    with db.session_scope() as session:
        if configured:
            session.add(
                ReminderSetting(
                    user_id=user_id, enabled=enabled, to_email=f"{user_id}@example.com"
                )
            )
        session.add(
            Todo(
                user_id=user_id,
                title=f"{user_id}-deadline",
                due_at=now() + timedelta(hours=1),
            )
        )
        session.add(
            Todo(
                user_id=user_id,
                title=f"{user_id}-scheduled",
                remind_at=now() - timedelta(minutes=1),
            )
        )


def test_cycle_sends_to_each_owner_and_deduplicates(reminder_db, monkeypatch):
    from app.core.config import get_settings

    # 走真实发送分支，但截获 SMTP 边界，既验证收件人又杜绝真实外发。
    monkeypatch.setenv("MAIL_BACKEND", "smtp")
    get_settings.cache_clear()
    captured = []
    monkeypatch.setattr(
        reminders.mail_svc, "_send_smtp", lambda payload: captured.append(payload)
    )
    try:
        for user_id in ("alice", "bob", "root"):
            add_user(user_id)
        add_user("disabled", enabled=False)
        add_user("unconfigured", configured=False)

        result = scheduler.run_reminder_cycle()
        assert result == {"ok": True, "users": 3, "sent": 6, "errors": 0}
        assert len(captured) == 6
        for user_id in ("alice", "bob", "root"):
            own = [p for p in captured if p["to_email"] == f"{user_id}@example.com"]
            assert len(own) == 2
            assert any(f"{user_id}-deadline" in p["body"] for p in own)
            assert any(f"{user_id}-scheduled" in p["body"] for p in own)
            for other in {"alice", "bob", "root", "disabled", "unconfigured"} - {
                user_id
            }:
                assert all(f"{other}-" not in p["body"] for p in own)

        with db.session_scope() as session:
            logs = session.exec(select(ReminderLog)).all()
            assert len(logs) == 6
            assert {row.user_id for row in logs} == {"alice", "bob", "root"}
            assert all(row.status == "sent" for row in logs)

        assert scheduler.run_reminder_cycle()["sent"] == 0
        assert len(captured) == 6
        reminders.disable_reminder("bob")
        assert scheduler.run_reminder_cycle()["users"] == 2
    finally:
        get_settings.cache_clear()


def test_cycle_isolates_user_and_reminder_kind_failures(reminder_db, monkeypatch):
    add_user("alice")
    add_user("bob")
    calls = []

    def deadline(user_id):
        calls.append((user_id, "deadline"))
        if user_id == "alice":
            raise RuntimeError("simulated database failure")
        return {"ok": True, "sent": 1}

    def scheduled(user_id):
        calls.append((user_id, "scheduled"))
        return {"ok": False, "sent": 0, "error": "simulated failure"}

    monkeypatch.setattr(scheduler, "run_deadline_reminders", deadline)
    monkeypatch.setattr(scheduler, "run_scheduled_reminders", scheduled)
    assert scheduler.run_reminder_cycle() == {
        "ok": False,
        "users": 2,
        "sent": 1,
        "errors": 3,
    }
    assert calls == [
        ("alice", "deadline"),
        ("alice", "scheduled"),
        ("bob", "deadline"),
        ("bob", "scheduled"),
    ]


def test_cycle_blocks_when_schema_not_ready(reminder_db, monkeypatch):
    add_user("alice")
    monkeypatch.setattr(db, "_reminder_schema_ready", False)
    monkeypatch.setattr(db, "_reminder_schema_error", "simulated migration failure")

    def must_not_send(_user_id):
        pytest.fail("schema unavailable must block all sending")

    monkeypatch.setattr(scheduler, "run_deadline_reminders", must_not_send)
    monkeypatch.setattr(scheduler, "run_scheduled_reminders", must_not_send)
    assert scheduler.run_reminder_cycle()["skipped"] == "schema_not_ready"


def test_cycle_without_enabled_users_does_not_fall_back_to_dev(reminder_db):
    add_user("alice", enabled=False)
    assert scheduler.run_reminder_cycle() == {
        "ok": True,
        "users": 0,
        "sent": 0,
        "errors": 0,
    }


@pytest.mark.asyncio
async def test_lifespan_starts_and_stops_multi_user_scheduler(monkeypatch):
    from app import main
    from app.core.config import get_settings
    monkeypatch.setenv("REMINDER_SCHEDULER_ENABLED", "true")
    get_settings.cache_clear()

    started = asyncio.Event()
    stopped = asyncio.Event()

    async def fake_loop():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(main, "reminder_loop", fake_loop)
    monkeypatch.setattr(main, "init_db", lambda: None)
    async with main.lifespan(main.app):
        await asyncio.wait_for(started.wait(), timeout=2)
        assert not stopped.is_set()
    assert stopped.is_set()
