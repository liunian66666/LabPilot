"""Streaming uses model chunks and saves the completed response before done."""
import asyncio
import json
from uuid import uuid4

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langgraph.checkpoint.memory import InMemorySaver

from app.agent import graph
from app.schemas import ChatMessage, ChatRequest
from app.services import chat_store
from app.services.chat_stream import stream_chat_events


def test_final_text_never_reuses_previous_turn():
    import pytest
    from langchain_core.messages import AIMessage, HumanMessage

    from app.core.llm import IncompleteReplyError

    old = [HumanMessage(content="清理重复待办"), AIMessage(content="清理完成")]
    current = [HumanMessage(content="设置截止提醒"), AIMessage(content="", response_metadata={"finish_reason": "length"})]
    with pytest.raises(IncompleteReplyError, match="生成上限"):
        graph._final_text({"messages": old + current})
    with pytest.raises(IncompleteReplyError, match="未生成正文"):
        graph._final_text({"messages": old + [HumanMessage(content="新请求"), AIMessage(content="")]})
    with pytest.raises(IncompleteReplyError, match="未生成有效回复"):
        graph._final_text({"messages": old + [HumanMessage(content="新请求")]})
    assert graph._final_text({"messages": old + [HumanMessage(content="新请求"), AIMessage(content="请提供日期")]}) == "请提供日期"


def test_generation_limit_is_sent_as_error_not_old_success():
    from app.core.llm import IncompleteReplyError

    tid = uuid4().hex
    body = ChatRequest(session_id=tid, messages=[ChatMessage(role="user", content="设置提醒")])

    class Stub:
        def chat_stream(self, **kwargs):
            if False:
                yield
            raise IncompleteReplyError("本次回复达到模型生成上限")

    async def collect():
        return [chunk async for chunk in stream_chat_events(Stub(), body, "dev")]

    frames = asyncio.run(collect())
    event = json.loads(frames[-1][6:])
    assert event == {"type": "error", "text": "本次回复达到模型生成上限"}
    assert [m["role"] for m in chat_store.list_messages(tid)] == ["user"]


def test_graph_stream_emits_multiple_model_deltas(monkeypatch):
    class FakeModel(FakeListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    monkeypatch.setattr(graph, "get_chat_model", lambda: FakeModel(responses=["你好，流式回复。"]))
    agent = graph.LabPilotAgent.__new__(graph.LabPilotAgent)
    agent._saver = InMemorySaver()
    agent._graph = agent._build()
    events = list(agent.chat_stream(user_id="dev", session_id=uuid4().hex, user_text="你好"))
    deltas = [e["text"] for e in events if e["type"] == "delta"]
    assert len(deltas) > 1
    assert "".join(deltas) == "你好，流式回复。"
    assert events[-1]["type"] == "done"
    assert events[-1]["data"]["reply"] == "你好，流式回复。"


def test_sse_done_saves_complete_history_and_artifacts():
    tid = uuid4().hex
    body = ChatRequest(session_id=tid, messages=[ChatMessage(role="user", content="生成周报")])

    class Stub:
        def chat_stream(self, **kwargs):
            yield {"type": "delta", "text": "已生成"}
            yield {"type": "done", "data": {
                "reply": "已生成周报", "session_id": tid, "tools_used": [],
                "artifacts": [{"type": "report", "id": "test-report", "title": "周报"}],
            }}

    async def collect():
        return [chunk async for chunk in stream_chat_events(Stub(), body, "dev")]

    frames = asyncio.run(collect())
    assert json.loads(frames[0][6:])["type"] == "delta"
    assert json.loads(frames[-1][6:])["type"] == "done"
    history = chat_store.list_messages(tid)
    assert [r["role"] for r in history] == ["user", "assistant", "card"]
    assert history[1]["content"] == "已生成周报"
    assert history[2]["card"]["id"] == "test-report"


def test_stream_tool_interrupt_returns_approval_card(monkeypatch):
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessageChunk
    from langchain_core.outputs import ChatGenerationChunk

    class ToolModel(BaseChatModel):
        @property
        def _llm_type(self):
            return "approval-stream-test"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, **kwargs):
            raise AssertionError("Model must stream")

        def _stream(self, messages, **kwargs):
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[{
                "name": "prepare_email_tool", "id": "approval-call", "index": 0,
                "args": json.dumps({"to_email": "teacher@example.com", "subject": "流式拟信", "body": "等待确认"}),
            }]))

    monkeypatch.setattr(graph, "get_chat_model", lambda: ToolModel())
    agent = graph.LabPilotAgent.__new__(graph.LabPilotAgent)
    agent._saver = InMemorySaver()
    agent._graph = agent._build()
    events = list(agent.chat_stream(user_id=uuid4().hex, session_id=uuid4().hex, user_text="准备邮件"))
    done = events[-1]["data"]
    assert done["pending_approval_id"]
    assert "确认" in done["reply"]
    assert any(a["type"] == "approval" for a in done["artifacts"])


def test_stream_error_is_explicit_and_no_success_saved():
    tid = uuid4().hex
    body = ChatRequest(session_id=tid, messages=[ChatMessage(role="user", content="你好")])

    class Stub:
        def chat_stream(self, **kwargs):
            yield {"type": "delta", "text": "部分回复"}
            raise RuntimeError("provider unavailable")

    async def collect():
        return [chunk async for chunk in stream_chat_events(Stub(), body, "dev")]

    frames = asyncio.run(collect())
    assert json.loads(frames[-1][6:])["type"] == "error"
    assert len(chat_store.list_messages(tid)) == 1
