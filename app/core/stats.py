"""进程内调用计数（对照评测用；不替代链路追踪）。"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_counts: dict[str, int] = {
    "llm_invoke": 0,
    "tool_invoke": 0,
    "llm_errors": 0,
}


def inc(key: str, n: int = 1) -> None:
    with _lock:
        _counts[key] = _counts.get(key, 0) + n


def snapshot() -> dict[str, int]:
    with _lock:
        return dict(_counts)


def delta(before: dict[str, int]) -> dict[str, int]:
    now = snapshot()
    return {k: now.get(k, 0) - before.get(k, 0) for k in set(now) | set(before)}


def reset() -> None:
    with _lock:
        for k in _counts:
            _counts[k] = 0
