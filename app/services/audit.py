"""审计事件写入。"""

from typing import Any

from app.models.db import session_scope
from app.models.tables import AuditEvent


def audit(user_id: str, event_type: str, session_id: str = "", **payload: Any) -> None:
    with session_scope() as s:
        s.add(
            AuditEvent(
                user_id=user_id,
                session_id=session_id,
                event_type=event_type,
                payload=payload,
            )
        )
