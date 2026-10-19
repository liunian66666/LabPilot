"""执行预算、并发准入、限流、断连与消息协议恢复。"""

import asyncio
from threading import Event
from uuid import uuid4

import pytest
from fastapi import HTTPException
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.agent import graph
from app.agent.budget import BudgetExceeded
from app.core import admission
from app.core.config import get_settings
from app.schemas import ChatMessage, ChatRequest


def test_model_budget_stops_loop_and_next_turn_can_recover(monkeypatch):
    monkeypatch.setenv("AGENT_MAX_LLM_CALLS", "2")
    get_settings.cache_clear()

    class LoopModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    model = LoopModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "draft_weekly_report_tool",
                        "args": {},
                        "id": f"loop-{index}",
                    }
                ],
            )
            for index in range(4)
        ]
    )
    monkeypatch.setattr(graph, "get_chat_model", lambda: model)
    agent = graph.LabPilotAgent.__new__(graph.LabPilotAgent)
    agent._saver = InMemorySaver()
    agent._graph = agent._build()
    user, tid = uuid4().hex, uuid4().hex
    with pytest.raises(BudgetExceeded, match="模型调用上限"):
        agent.chat(user_id=user, session_id=tid, user_text="循环生成")
    snapshot = agent._graph.get_state(agent._config(tid, user))
    assert (
        len([m for m in snapshot.values["messages"] if isinstance(m, AIMessage)]) == 2
    )
    model.responses = [AIMessage(content="已停止，请检查已有周报。")]
    model.i = 0
    assert "已停止" in agent.chat(user_id=user, session_id=tid, user_text="停止").reply


def test_tool_budget_prevents_remaining_side_effects(monkeypatch):
    from langchain_core.tools import tool

    from app.agent.artifacts import end_run
    from app.agent.budget import begin_budget, end_budget
    from app.agent.tool_runner import execute_tool_calls

    monkeypatch.setenv("AGENT_MAX_TOOL_CALLS", "1")
    get_settings.cache_clear()
    executed = []

    @tool
    def count_call(value: int) -> str:
        """Record a deterministic side effect."""
        executed.append(value)
        return "ok"

    token = begin_budget()
    run_id = uuid4().hex
    try:
        with pytest.raises(BudgetExceeded, match="工具调用上限"):
            execute_tool_calls(
                [
                    {"name": "count_call", "args": {"value": 1}, "id": "one"},
                    {"name": "count_call", "args": {"value": 2}, "id": "two"},
                ],
                run_id,
                allowed_tools=[count_call],
            )
        assert executed == [1]
    finally:
        end_budget(token)
        end_run(run_id)


def test_expired_budget_blocks_new_operation():
    from app.agent.budget import RunBudget

    budget = RunBudget(2, 2, 1, started=-100000)
    with pytest.raises(BudgetExceeded, match="时间预算"):
        budget.consume("tool")
    assert budget.tools == 0


def test_admission_limits_and_release(monkeypatch):
    monkeypatch.setenv("CHAT_MAX_CONCURRENT", "2")
    monkeypatch.setenv("CHAT_MAX_PER_USER", "1")
    get_settings.cache_clear()
    user = uuid4().hex
    first = admission.acquire(user, "one")
    try:
        with pytest.raises(HTTPException) as conflict:
            admission.acquire(user, "one")
        assert conflict.value.status_code == 409
        with pytest.raises(HTTPException) as busy:
            admission.acquire(user, "two")
        assert busy.value.status_code == 429
        other = admission.acquire(uuid4().hex, "other")
        try:
            with pytest.raises(HTTPException):
                admission.acquire(uuid4().hex, "full")
        finally:
            other.release()
    finally:
        first.release()
    admission.acquire(user, "two").release()


def test_login_ip_rate_limit_and_request_id(anon_client, monkeypatch):
    from app.core import request_context

    monkeypatch.setenv("AUTH_REQUESTS_PER_MINUTE", "2")
    get_settings.cache_clear()
    monkeypatch.setattr(request_context, "auth_rate", admission.SlidingWindow())
    for _ in range(2):
        response = anon_client.post(
            "/api/v1/auth/login", json={"username": "unknown", "password": "invalid"}
        )
        assert response.status_code == 401
        assert len(response.headers["x-request-id"]) == 32
    blocked = anon_client.post(
        "/api/v1/auth/login", json={"username": "unknown", "password": "invalid"}
    )
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"] == "60"


def test_stream_disconnect_keeps_admission_until_history_saved():
    from app.services import chat_store
    from app.services.chat_stream import stream_chat_events

    finish = Event()
    started = Event()
    user, tid = uuid4().hex, uuid4().hex
    lease = admission.acquire(user, tid)

    class Stub:
        def chat_stream(self, **kwargs):
            yield {"type": "delta", "text": "部分内容"}
            started.set()
            assert finish.wait(5)
            yield {"type": "done", "data": {"reply": "完成", "artifacts": []}}

    async def disconnect():
        body = ChatRequest(session_id=tid, messages=[ChatMessage(content="开始")])
        stream = stream_chat_events(Stub(), body, user, lease=lease)
        await anext(stream)
        await stream.aclose()
        assert started.wait(1)
        with pytest.raises(HTTPException):
            admission.acquire(user, tid)
        finish.set()
        for _ in range(100):
            if lease.released:
                break
            await asyncio.sleep(0.01)
        assert lease.released
        assert chat_store.list_messages(tid, user)[-1]["content"] == "完成"

    try:
        asyncio.run(disconnect())
    finally:
        finish.set()


def test_unmounted_tool_not_callable_even_if_global_registry_contains_it():
    from app.agent.artifacts import end_run
    from app.agent.tool_runner import execute_tool_calls

    run_id = uuid4().hex
    try:
        result = execute_tool_calls(
            [{"name": "draft_weekly_report_tool", "args": {}, "id": "blocked"}],
            run_id,
            allowed_tools=[],
        )
        assert "未知工具" in result[0].content
    finally:
        end_run(run_id)
