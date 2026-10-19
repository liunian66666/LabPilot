"""按 run_id 分桶的产物收集（线程安全 + 请求隔离）。"""

from __future__ import annotations

import threading
from typing import Any

_lock = threading.Lock()
_buckets: dict[str, list[dict[str, Any]]] = {}
_local = threading.local()


def begin_run(run_id: str) -> None:
    """标记当前线程归属的 run；同时开桶。"""
    _local.run_id = run_id
    with _lock:
        _buckets.setdefault(run_id, [])


def set_run_id(run_id: str) -> None:
    _local.run_id = run_id


def current_run_id() -> str | None:
    return getattr(_local, "run_id", None)


def add_artifact(
    type: str,
    id: str,
    title: str = "",
    status: str = "",
    content_hash: str = "",
    **meta: Any,
) -> None:
    rid = current_run_id()
    if not rid:
        # 未登记 run：丢弃而非写入公共桶，避免串请求
        return
    item = {
        "type": type,
        "id": str(id),
        "title": title,
        "status": status,
        "content_hash": content_hash,
        "meta": meta,
    }
    with _lock:
        _buckets.setdefault(rid, []).append(item)


def take_artifacts(run_id: str) -> list[dict[str, Any]]:
    with _lock:
        return list(_buckets.pop(run_id, []))


def end_run(run_id: str) -> None:
    with _lock:
        _buckets.pop(run_id, None)
    if getattr(_local, "run_id", None) == run_id:
        _local.run_id = None
