"""run 级状态快照：供工具读取图状态（如 pending_approval_id）。"""

from __future__ import annotations

import threading
from typing import Any

_local = threading.local()


def set_state_snapshot(state: dict[str, Any] | None) -> None:
    _local.state = dict(state or {})


def get_state_snapshot() -> dict[str, Any]:
    return getattr(_local, "state", None) or {}


def clear_state_snapshot() -> None:
    _local.state = {}
