"""截止提醒。

规则：
- 自助提醒：开启时一次性授权邮箱+提前量，到点**自动发送**（不逐次 HITL）
- 临时外发（邮件给他人）仍走 prepare_email + 人工审批
- 后台调度扫描；跳过 done/cancelled；reminder_logs 防重复；改期后 due_key 变化可再提醒
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlmodel import select

from app.core.config import get_settings
from app.core.dates import now
from app.core.logging import log_event
from app.models.db import session_scope
from app.models.tables import ReminderLog, ReminderSetting, Todo, TodoStatus
from app.services import mail as mail_svc


def get_reminder_setting(user_id: str = "dev") -> dict[str, Any]:
    with session_scope() as s:
        row = s.exec(
            select(ReminderSetting).where(ReminderSetting.user_id == user_id)
        ).first()
        if not row:
            return {
                "enabled": False,
                "to_email": "",
                "hours_before": get_settings().REMIND_BEFORE_HOURS,
            }
        return {
            "enabled": row.enabled,
            "to_email": row.to_email,
            "hours_before": row.hours_before,
        }


def enable_reminder(
    user_id: str,
    *,
    to_email: str,
    hours_before: int = 24,
) -> dict[str, Any]:
    """开启自助提醒（一次性授权自动发送）。"""
    import re

    to_email = (to_email or "").strip()
    if not to_email or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", to_email):
        return {"ok": False, "error": "invalid_email"}
    try:
        hours_before = int(hours_before)
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid_hours_before"}
    if hours_before < 1 or hours_before > 24 * 14:
        return {"ok": False, "error": "hours_before_out_of_range"}
    with session_scope() as s:
        row = s.exec(
            select(ReminderSetting).where(ReminderSetting.user_id == user_id)
        ).first()
        if not row:
            row = ReminderSetting(user_id=user_id)
            s.add(row)
        row.enabled = True
        row.to_email = to_email
        row.hours_before = int(hours_before)
        row.updated_at = now()
        s.flush()
        cfg = {
            "enabled": True,
            "to_email": to_email,
            "hours_before": int(hours_before),
        }
    log_event("reminder_enabled", user_id=user_id, hours=hours_before)
    return {"ok": True, **cfg}


def disable_reminder(user_id: str) -> dict[str, Any]:
    with session_scope() as s:
        row = s.exec(
            select(ReminderSetting).where(ReminderSetting.user_id == user_id)
        ).first()
        if row:
            row.enabled = False
            row.updated_at = now()
    log_event("reminder_disabled", user_id=user_id)
    return {"ok": True, "enabled": False}


def _due_key(t: Todo) -> str:
    if t.due_at:
        return f"due:{t.due_at.isoformat()}"
    if t.planned_date:
        return f"plan:{t.planned_date.isoformat()}"
    return "none"


def todos_need_reminder(
    user_id: str = "dev",
    *,
    hours: int | None = None,
    include_overdue: bool = True,
) -> list[dict[str, Any]]:
    s = get_settings()
    if hours is None:
        cfg = get_reminder_setting(user_id)
        hours = int(cfg.get("hours_before") or s.REMIND_BEFORE_HOURS)
    horizon = now() + timedelta(hours=hours)
    today = now().date()
    out: list[dict[str, Any]] = []
    with session_scope() as sess:
        rows = sess.exec(select(Todo).where(Todo.user_id == user_id)).all()
        for t in rows:
            if str(t.status) in (TodoStatus.DONE.value, TodoStatus.CANCELLED.value):
                continue
            due = t.due_at
            planned = t.planned_date
            need = False
            if due and due <= horizon:
                need = include_overdue or due >= now()
            if planned and not due and planned <= today:
                need = include_overdue or planned >= today
            if need:
                out.append(
                    {
                        "id": str(t.id),
                        "title": t.title,
                        "due_at": due.isoformat() if due else None,
                        "planned_date": planned.isoformat() if planned else None,
                        "priority": str(t.priority),
                        "status": str(t.status),
                        "due_key": _due_key(t),
                    }
                )
    rank = {"urgent_important": 0, "important": 1, "urgent": 2, "normal": 3}
    out.sort(
        key=lambda x: (
            rank.get(x["priority"], 9),
            x.get("due_at") or x.get("planned_date") or "",
        )
    )
    return out


def build_reminder_body(items: list[dict[str, Any]]) -> str:
    lines = ["你好，以下待办临近或已过截止时间，请处理：", ""]
    for i, it in enumerate(items, 1):
        when = it.get("due_at") or it.get("planned_date") or "无日期"
        lines.append(f"{i}. [{it['priority']}] {it['title']}（{when}，状态 {it['status']}）")
    lines.append("")
    lines.append("—— LabPilot 待办提醒（你已授权自动发送）")
    return "\n".join(lines)


def _already_sent(user_id: str, todo_id: str, due_key: str) -> bool:
    with session_scope() as s:
        try:
            tid = UUID(str(todo_id))
        except ValueError:
            return False
        row = s.exec(
            select(ReminderLog)
            .where(ReminderLog.user_id == user_id)
            .where(ReminderLog.todo_id == tid)
            .where(ReminderLog.due_key == due_key)
            .where(ReminderLog.status.in_(["sent", "sending", "unknown"]))
        ).first()
        return row is not None


def _claim_reminder(user_id: str, todo_id: str, due_key: str) -> bool:
    """发送前原子领取。

    - 无记录：INSERT sending（唯一约束防并发）
    - 仅有 failed：UPDATE failed→sending（允许明确失败重试）
    - 已有 sent/sending/unknown：不领取（unknown 待人工核查）
    """
    import uuid

    from sqlalchemy import text

    from app.models.db import get_engine

    ts = now().isoformat()
    key = UUID(str(todo_id)).hex
    try:
        with get_engine().begin() as conn:
            # 失败可重试
            r_upd = conn.execute(
                text(
                    "UPDATE reminder_logs SET status='sending', detail='', sent_at=:ts "
                    "WHERE user_id=:u AND todo_id=:t AND due_key=:d AND status='failed'"
                ),
                {"ts": ts, "u": user_id, "t": key, "d": due_key},
            )
            if int(getattr(r_upd, "rowcount", 0) or 0) == 1:
                return True
            conn.execute(
                text(
                    "INSERT INTO reminder_logs (id, user_id, todo_id, due_key, sent_at, status, detail) "
                    "VALUES (:id, :u, :t, :d, :ts, 'sending', '')"
                ),
                {
                    "id": uuid.uuid4().hex,
                    "u": user_id,
                    "t": key,
                    "d": due_key,
                    "ts": ts,
                },
            )
            return True
    except Exception:  # noqa: BLE001  唯一约束冲突=已有 sent/sending/unknown
        return False


def _finish_reminder(
    user_id: str, todo_id: str, due_key: str, status: str, detail: str = ""
) -> None:
    from sqlalchemy import text

    from app.models.db import get_engine

    key = UUID(str(todo_id)).hex
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "UPDATE reminder_logs SET status=:s, detail=:det "
                "WHERE user_id=:u AND todo_id=:t AND due_key=:d AND status='sending'"
            ),
            {"s": status, "det": detail[:500], "u": user_id, "t": key, "d": due_key},
        )


def run_deadline_reminders(user_id: str = "dev") -> dict[str, Any]:
    """后台调度入口：授权后自动发送；CAS 领取防并发重复。"""
    from app.models.db import reminder_schema_status

    ready, err = reminder_schema_status()
    if not ready:
        # 迁移失败：停止提醒调度，避免无唯一索引重复发送
        return {
            "ok": False,
            "skipped": "schema_not_ready",
            "sent": 0,
            "error": err or "reminder schema migration failed",
        }
    cfg = get_reminder_setting(user_id)
    if not cfg.get("enabled"):
        return {"ok": True, "skipped": "disabled", "sent": 0}
    to = cfg.get("to_email") or get_settings().REMIND_DEFAULT_TO
    if not to:
        return {"ok": False, "error": "no_recipient", "sent": 0}

    items = todos_need_reminder(user_id, hours=int(cfg.get("hours_before") or 24))
    claimed: list[dict[str, Any]] = []
    for it in items:
        if _claim_reminder(user_id, it["id"], it["due_key"]):
            claimed.append(it)
    if not claimed:
        return {
            "ok": True,
            "sent": 0,
            "candidates": len(items),
            "skipped_duplicate": True,
        }

    subject = f"待办提醒：{len(claimed)} 项临近/逾期"
    body = build_reminder_body(claimed)
    try:
        if get_settings().MAIL_BACKEND == "smtp":
            mail_svc._send_smtp(
                {"to_email": to, "subject": subject, "body": body, "to_name": ""}
            )
        else:
            log_event(
                "deadline_reminder_mock_send",
                user_id=user_id,
                to=to,
                count=len(claimed),
            )
        for it in claimed:
            _finish_reminder(user_id, it["id"], it["due_key"], "sent", subject)
        return {
            "ok": True,
            "sent": len(claimed),
            "to": to,
            "subject": subject,
            "todo_ids": [it["id"] for it in claimed],
        }
    except Exception as e:  # noqa: BLE001
        from app.services.mail import classify_send_error

        msg = str(e)
        end = classify_send_error(e, getattr(e, "labpilot_send_stage", None))
        for it in claimed:
            _finish_reminder(user_id, it["id"], it["due_key"], end, msg)
        log_event("deadline_reminder_failed", user_id=user_id, error=msg, end_state=end)
        return {"ok": False, "sent": 0, "error": msg, "failed_state": end}


def _at_key(remind_at: datetime) -> str:
    """自定义提醒的防重复 key：改期后 key 变化可再提醒。"""
    return f"at:{remind_at.isoformat()}"


def schedule_todo_reminder(user_id: str, todo_id: str, remind_at: datetime) -> dict[str, Any]:
    """为待办设置自定义提醒时刻（精确到分钟，到点自动发邮件）。"""
    with session_scope() as s:
        try:
            tid = UUID(str(todo_id))
        except ValueError:
            return {"ok": False, "error": "invalid_todo_id"}
        t = s.get(Todo, tid)
        if not t or t.user_id != user_id:
            return {"ok": False, "error": "todo_not_found"}
        if str(t.status) in (TodoStatus.DONE.value, TodoStatus.CANCELLED.value):
            return {"ok": False, "error": "todo_closed"}
        t.remind_at = remind_at
        t.updated_at = now()
        title = t.title
    log_event(
        "reminder_scheduled",
        user_id=user_id,
        todo_id=str(todo_id),
        remind_at=remind_at.isoformat(),
    )
    return {"ok": True, "todo_id": str(todo_id), "title": title, "remind_at": remind_at.isoformat()}


def run_scheduled_reminders(user_id: str = "dev") -> dict[str, Any]:
    """自定义时刻提醒：remind_at 到点即发（与截止提前量机制相互独立）。

    复用 reminder_logs（due_key=at:<iso>）原子领取，防重复发送；
    逐条发送，单条失败不影响其余。
    """
    from app.models.db import reminder_schema_status

    ready, err = reminder_schema_status()
    if not ready:
        return {"ok": False, "skipped": "schema_not_ready", "sent": 0, "error": err}
    cfg = get_reminder_setting(user_id)
    if not cfg.get("enabled"):
        return {"ok": True, "skipped": "disabled", "sent": 0}
    to = cfg.get("to_email") or get_settings().REMIND_DEFAULT_TO
    if not to:
        return {"ok": False, "error": "no_recipient", "sent": 0}

    cur = now()
    due_items: list[dict[str, Any]] = []
    with session_scope() as sess:
        rows = sess.exec(
            select(Todo)
            .where(Todo.user_id == user_id)
            .where(Todo.remind_at.is_not(None))  # type: ignore[union-attr]
        ).all()
        for t in rows:
            if str(t.status) in (TodoStatus.DONE.value, TodoStatus.CANCELLED.value):
                continue
            if t.remind_at and t.remind_at <= cur:
                due_items.append({"id": str(t.id), "title": t.title, "remind_at": t.remind_at})

    claimed: list[dict[str, Any]] = []
    for it in due_items:
        key = _at_key(it["remind_at"])
        if _claim_reminder(user_id, it["id"], key):
            claimed.append({**it, "due_key": key})
    if not claimed:
        return {"ok": True, "sent": 0, "due": len(due_items), "skipped_duplicate": not due_items}

    sent, failed = 0, 0
    for it in claimed:
        subject = f"定时提醒：{it['title']}"
        body = (
            f"这是你在 {it['remind_at'].strftime('%Y-%m-%d %H:%M')} 安排的提醒：\n\n"
            f"【{it['title']}】\n\n—— LabPilot 定时提醒（你已授权自动发送）"
        )
        try:
            if get_settings().MAIL_BACKEND == "smtp":
                mail_svc._send_smtp({"to_email": to, "subject": subject, "body": body, "to_name": ""})
            else:
                log_event(
                    "scheduled_reminder_mock_send",
                    user_id=user_id,
                    to=to,
                    todo_id=it["id"],
                )
            _finish_reminder(user_id, it["id"], it["due_key"], "sent", subject)
            sent += 1
        except Exception as e:  # noqa: BLE001
            from app.services.mail import classify_send_error

            end = classify_send_error(e, getattr(e, "labpilot_send_stage", None))
            _finish_reminder(user_id, it["id"], it["due_key"], end, str(e))
            failed += 1
            log_event(
                "scheduled_reminder_failed",
                user_id=user_id,
                todo_id=it["id"],
                error=str(e),
                end_state=end,
            )
    return {"ok": failed == 0, "sent": sent, "failed": failed, "to": to}


# —— 兼容旧接口：调试用，改走审批式（仍保留）——
def prepare_deadline_reminders(
    *,
    user_id: str = "dev",
    session_id: str = "reminders",
    to_email: str = "",
    hours: int | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    to = to_email or settings.REMIND_DEFAULT_TO
    if not to:
        return {"ok": False, "error": "no_recipient"}
    items = todos_need_reminder(user_id, hours=hours)
    if not items:
        return {"ok": True, "created": 0, "items": [], "approvals": []}
    subject = f"待办提醒（需确认）：{len(items)} 项临近/逾期"
    body = build_reminder_body(items)
    app = mail_svc.prepare_email(
        user_id=user_id,
        session_id=session_id,
        to_email=to,
        subject=subject,
        body=body,
    )
    return {
        "ok": True,
        "created": 1,
        "items": items,
        "approvals": [
            {
                "approval_id": str(app.id),
                "payload_version": app.payload_version,
                "status": str(app.status),
            }
        ],
    }
