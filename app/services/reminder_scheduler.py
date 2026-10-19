"""后台提醒调度：枚举已授权用户，复用按用户隔离的发送服务。"""

import asyncio
from typing import Any

from sqlmodel import select

from app.core.logging import log_event
from app.models.db import reminder_schema_status, session_scope
from app.models.tables import ReminderSetting
from app.services.reminders import run_deadline_reminders, run_scheduled_reminders


def run_reminder_cycle() -> dict[str, Any]:
    """执行一轮检查；用户及两类提醒之间的异常互不阻断。

    仅扫描开启提醒的配置，不创建默认用户或默认授权。
    发送服务会重新检查授权，并通过现有 CAS 领取机制防止重复发送。
    """
    ready, error = reminder_schema_status()
    if not ready:
        log_event("reminder_cycle_blocked", reason="schema_not_ready", error=error)
        return {"ok": False, "skipped": "schema_not_ready", "users": 0, "sent": 0}

    with session_scope() as session:
        user_ids = list(
            session.exec(
                select(ReminderSetting.user_id)
                .where(ReminderSetting.enabled == True)
                .distinct()
                .order_by(ReminderSetting.user_id)
            ).all()
        )

    sent = errors = 0
    for user_id in user_ids:
        for kind, runner in (
            ("deadline", run_deadline_reminders),
            ("scheduled", run_scheduled_reminders),
        ):
            try:
                result = runner(user_id)
                sent += int(result.get("sent", 0))
                if not result.get("ok", False):
                    errors += 1
                if result.get("sent") or not result.get("ok", False):
                    log_event(
                        "reminder_cycle_result", user_id=user_id, kind=kind, **result
                    )
            except Exception as exc:  # noqa: BLE001
                errors += 1
                log_event(
                    "reminder_cycle_error", user_id=user_id, kind=kind, error=str(exc)
                )
    return {"ok": errors == 0, "users": len(user_ids), "sent": sent, "errors": errors}


async def reminder_loop(interval_s: float = 60) -> None:
    """同步 DB/SMTP 在线程执行；每轮完成后再等待，不重叠启动扫描。"""
    while True:
        try:
            await asyncio.to_thread(run_reminder_cycle)
        except Exception as exc:  # noqa: BLE001
            log_event("reminder_loop_error", error=str(exc))
        await asyncio.sleep(interval_s)
