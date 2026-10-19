"""工具分发：在当前线程 + 复制上下文中执行，保证 run_id 传入工具。"""

from __future__ import annotations

from contextvars import copy_context
from typing import Any

from langchain_core.messages import ToolMessage

from app.agent.artifacts import begin_run, set_run_id
from app.agent.budget import consume
from app.core.logging import log_event, log_exception

TOOL_BY_NAME: dict[str, Any] = {}


def register_tools(tools: list[Any]) -> None:
    for t in tools:
        TOOL_BY_NAME[t.name] = t


def execute_tool_calls(
    tool_calls: list[dict],
    run_id: str,
    state: dict[str, Any] | None = None,
    allowed_tools: list[Any] | None = None,
) -> list[ToolMessage]:
    """同步执行工具调用，返回 ToolMessage 列表。

    artifacts 不在此处收集：工具通过 add_artifact 写入按 run_id 分桶的
    注册表，由 chat/chat_stream 结束时 take_artifacts(run_id) 统一取出，
    保证每轮只返回本轮新增的产物（不跨轮累积）。
    """

    def _exec() -> list[ToolMessage]:
        from app.agent.runctx import clear_state_snapshot, set_state_snapshot

        begin_run(run_id)
        set_run_id(run_id)
        set_state_snapshot(state)
        messages: list[ToolMessage] = []
        try:
            for tc in tool_calls or []:
                name = tc.get("name") or ""
                args = tc.get("args") or {}
                call_id = tc.get("id") or "unknown"
                registry = (
                    TOOL_BY_NAME
                    if allowed_tools is None
                    else {t.name: t for t in allowed_tools}
                )
                tool = registry.get(name)
                if tool is None:
                    messages.append(
                        ToolMessage(content=f"未知工具: {name}", tool_call_id=call_id)
                    )
                    continue
                consume("tool")
                try:
                    from app.core.stats import inc

                    inc("tool_invoke")
                    log_event("tool_invoke_start", tool=name, run_id=run_id)
                    out = tool.invoke(args)
                    text = out if isinstance(out, str) else str(out)
                    log_event(
                        "tool_invoke_done",
                        tool=name,
                        run_id=run_id,
                        result_len=len(text),
                    )
                except Exception as e:
                    # HITL interrupt 必须上抛，否则图无法暂停
                    if "interrupt" in type(e).__name__.lower() or type(e).__name__ in (
                        "GraphInterrupt",
                        "GraphBubbleUp",
                    ):
                        log_event("tool_interrupt", tool=name, run_id=run_id)
                        raise
                    log_event(
                        "tool_invoke_failed", tool=name, run_id=run_id, error=str(e)
                    )
                    log_exception("tool_invoke_exception", tool=name, run_id=run_id)
                    text = f"工具执行失败: {e}"
                messages.append(ToolMessage(content=text, tool_call_id=call_id))
            return messages
        finally:
            clear_state_snapshot()

    return copy_context().run(_exec)
