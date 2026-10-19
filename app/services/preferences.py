"""长期偏好：只持久化用户明确确认的稳定偏好。"""

from __future__ import annotations

from typing import Any

from sqlmodel import select

from app.core.dates import now
from app.models.db import session_scope
from app.models.tables import UserPreference

# 仅允许这些 key，避免把临时要求误存成永久偏好
ALLOWED_KEYS = {
    "research_focus",
    "report_sections",
    "advisor_salutation",
    "report_tone",
    "default_report_period",
    "email_signoff",
}

# 「以后都 / 一直 / 默认」才视为长期偏好
PERMANENT_CUES = ("以后", "一直", "总是", "默认", "都按", "都用", "每次都")


def is_permanent_request(text: str) -> bool:
    return any(c in text for c in PERMANENT_CUES)


def get_preferences(user_id: str = "dev") -> dict[str, str]:
    with session_scope() as s:
        rows = s.exec(select(UserPreference).where(UserPreference.user_id == user_id)).all()
        return {r.key: r.value for r in rows}


def set_preference(user_id: str, key: str, value: str) -> dict[str, Any]:
    if key not in ALLOWED_KEYS:
        return {"ok": False, "error": "key_not_allowed", "allowed": sorted(ALLOWED_KEYS)}
    value = (value or "").strip()
    if not value:
        return {"ok": False, "error": "empty_value"}
    with session_scope() as s:
        row = s.exec(
            select(UserPreference)
            .where(UserPreference.user_id == user_id)
            .where(UserPreference.key == key)
        ).first()
        if row:
            row.value = value
            row.updated_at = now()
        else:
            row = UserPreference(user_id=user_id, key=key, value=value)
            s.add(row)
        s.flush()
        return {"ok": True, "key": key, "value": value}


def delete_preference(user_id: str, key: str) -> dict[str, Any]:
    with session_scope() as s:
        row = s.exec(
            select(UserPreference)
            .where(UserPreference.user_id == user_id)
            .where(UserPreference.key == key)
        ).first()
        if row:
            s.delete(row)
        return {"ok": True, "deleted": key}
