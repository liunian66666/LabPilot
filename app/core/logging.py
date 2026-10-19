"""结构化日志（对齐 FastGraph 规范）。

- 事件名固定英文 snake_case，禁止 f-string 拼事件名
- 动态字段用 kwargs（user_id=...）
- except 中用 log_exception 保留堆栈，禁止只 log error 字符串
"""

from __future__ import annotations

import sys
from typing import Any

from loguru import logger

from app.core.config import get_settings

_configured = False


def setup_logging() -> None:
    global _configured
    if _configured:
        return
    settings = get_settings()
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.LOG_LEVEL,
        diagnose=False,
        backtrace=False,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message} | {extra}",
    )
    # 可选：写入文件
    log_dir = getattr(settings, "LOG_DIR", None) or "logs"
    try:
        from pathlib import Path

        Path(log_dir).mkdir(parents=True, exist_ok=True)
        # 这是 **loguru**（第三方日志库）的 `logger.add(...)` 配置，作用是**把日志同时写入一个 JSON Lines 文件**，并设置级别、序列化和轮转。
        logger.add(
            str(Path(log_dir) / "labpilot.jsonl"),
            level=settings.LOG_LEVEL,
            serialize=True,  # ③ 写成 JSON
            diagnose=False,
            backtrace=False,
            rotation="10 MB",  # ④ 文件轮转，10MB 后创建新文件
        )
    except Exception:  # noqa: BLE001, S110 - 文件日志不可用时仍保留 stderr
        pass
    _configured = True


def log_event(event: str, **fields: Any) -> None:
    """普通事件：固定 event 名 + kwargs。"""
    from app.core.request_context import request_id

    fields.setdefault("request_id", request_id.get())
    logger.bind(**fields).info(event)


def log_warning(event: str, **fields: Any) -> None:
    logger.bind(**fields).warning(event)


def log_exception(event: str, **fields: Any) -> None:
    """except 中使用，保留完整堆栈。"""
    logger.bind(**fields).exception(event)
