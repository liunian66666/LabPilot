"""单进程准入与限流；多实例部署需共享限流与执行协调存储。"""

from collections import deque
from functools import wraps
from inspect import signature
from threading import Lock
from time import monotonic

from fastapi import HTTPException

from app.core.config import get_settings


class SlidingWindow:
    def __init__(self):
        self._lock = Lock()
        self._hits = {}

    def check(self, key: str, limit: int):
        now = monotonic()
        with self._lock:
            for old in [
                k
                for k, values in self._hits.items()
                if not values or values[-1] <= now - 60
            ]:
                del self._hits[old]
            hits = self._hits.get(key)
            if hits is None:
                if len(self._hits) >= 8192:
                    raise HTTPException(
                        429, "请求繁忙，请稍后重试。", headers={"Retry-After": "60"}
                    )
                hits = self._hits[key] = deque()
            while hits and hits[0] <= now - 60:
                hits.popleft()
            if len(hits) >= limit:
                raise HTTPException(
                    429, "请求过于频繁，请稍后重试。", headers={"Retry-After": "60"}
                )
            hits.append(now)


auth_rate = SlidingWindow()
chat_rate = SlidingWindow()
_lock = Lock()
_active: set[tuple[str, str]] = set()


class Lease:
    def __init__(self, key):
        self.key = key
        self.started = False
        self.released = False

    def mark_started(self):
        with _lock:
            self.started = True

    def release(self):
        with _lock:
            if not self.released:
                _active.discard(self.key)
                self.released = True

    def release_if_unstarted(self):
        with _lock:
            if not self.started and not self.released:
                _active.discard(self.key)
                self.released = True


def acquire(user_id: str, session_id: str) -> Lease:
    settings = get_settings()
    chat_rate.check(user_id, settings.CHAT_REQUESTS_PER_MINUTE)
    key = (user_id, session_id)
    with _lock:
        if key in _active:
            raise HTTPException(409, "当前会话仍在执行，请等待完成后再继续。")
        if (
            len(_active) >= settings.CHAT_MAX_CONCURRENT
            or sum(k[0] == user_id for k in _active) >= settings.CHAT_MAX_PER_USER
        ):
            raise HTTPException(
                429, "当前执行任务较多，请稍后重试。", headers={"Retry-After": "5"}
            )
        _active.add(key)
    return Lease(key)


def guard_chat(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        values = signature(function).bind(*args, **kwargs).arguments
        lease = acquire(values["current_user"], values["body"].session_id)
        try:
            return function(*args, **kwargs)
        finally:
            lease.release()

    return guarded
