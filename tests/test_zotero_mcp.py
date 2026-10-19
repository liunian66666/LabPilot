"""Zotero MCP 客户端：按用户选择凭证，Agent 只得到当前库的结果。"""

import asyncio
import json
import socket
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
import uvicorn
from mcp import Client
from mcp.server import MCPServer

from app.agent import graph
from app.agent.artifacts import end_run
from app.agent.context import reset_actor_context, set_actor_context
from app.agent.tool_runner import execute_tool_calls, register_tools
from app.services import zotero_credentials, zotero_mcp
from app.zotero_server import server as zotero_server


@pytest.fixture(autouse=True)
def _offline_query_expansion(monkeypatch):
    """协议测试不读取本机 LLM 凭证或访问真实模型。"""
    async def no_expansion(_query):
        return ""

    monkeypatch.setattr(zotero_mcp, "_expand_bilingual_query", no_expansion)


def _settings():
    return SimpleNamespace(
        ZOTERO_MCP_URL="http://127.0.0.1:8765/mcp",
        ZOTERO_MCP_INTERNAL_TOKEN="test-internal-token",
        ZOTERO_MCP_TIMEOUT_SECONDS=2.0,
    )


def _fake_transport(monkeypatch, server: MCPServer, captured: list[tuple[str, str]]):
    @asynccontextmanager
    async def fake_client(api_key: str, zotero_user_id: str = ""):
        captured.append((api_key, zotero_user_id))
        async with Client(server) as client:
            yield client

    monkeypatch.setattr(zotero_mcp, "_client", fake_client)


@pytest.mark.parametrize("translated_query", ["", "ablation study"])
def test_zotero_search_uses_current_users_credential(monkeypatch, translated_query):
    server = MCPServer("test-zotero")
    captured: list[tuple[str, str]] = []
    calls: list[str] = []

    @server.tool()
    def zotero_search_items(query: str, limit: int = 10) -> dict:
        calls.append(query)
        return {"items": [{"zotero_key": "ABCD1234", "title": "消融实验方法"}]}

    async def fake_expansion(query):
        assert query == "消融实验"
        return translated_query

    monkeypatch.setattr(zotero_mcp, "_expand_bilingual_query", fake_expansion)
    monkeypatch.setattr(zotero_mcp, "get_settings", _settings)
    monkeypatch.setattr(
        zotero_credentials,
        "load_credential",
        lambda user: SimpleNamespace(zotero_user_id="111", api_key="alice-key") if user == "alice" else None,
    )
    _fake_transport(monkeypatch, server, captured)

    items = asyncio.run(zotero_mcp.search_reference_items(" 消融实验 ", "alice"))
    assert items == [{"zotero_key": "ABCD1234", "title": "消融实验方法"}]
    assert calls == (["消融实验", translated_query] if translated_query else ["消融实验"])
    assert captured == [("alice-key", "111")]
    assert asyncio.run(zotero_mcp.search_references("消融实验", "bob")) == "请先在设置中连接 Zotero。"


def test_zotero_key_verified_through_mcp(monkeypatch):
    server = MCPServer("test-zotero-identity")
    captured: list[tuple[str, str]] = []

    @server.tool()
    def zotero_connection_info() -> dict:
        return {"user_id": "12345"}

    monkeypatch.setattr(zotero_mcp, "get_settings", _settings)
    _fake_transport(monkeypatch, server, captured)

    assert asyncio.run(zotero_mcp.verify_zotero_credentials("private-key")) == {"user_id": "12345"}
    assert captured == [("private-key", "")]


def test_agent_tool_only_registered_for_bound_user(monkeypatch):
    server = MCPServer("test-zotero-dispatch")
    captured: list[tuple[str, str]] = []

    @server.tool()
    def zotero_search_items(query: str, limit: int = 10) -> dict:
        return {"items": [{"title": f"Zotero 命中：{query}"}]}

    monkeypatch.setattr(zotero_mcp, "get_settings", _settings)
    monkeypatch.setattr(zotero_credentials, "has_credential", lambda user: user == "alice")
    monkeypatch.setattr(
        zotero_credentials,
        "load_credential",
        lambda user: SimpleNamespace(zotero_user_id="111", api_key="alice-key") if user == "alice" else None,
    )
    _fake_transport(monkeypatch, server, captured)

    assert graph.search_zotero_references in graph._tools_for_user("alice")
    assert graph.search_zotero_references not in graph._tools_for_user("bob")
    register_tools([graph.search_zotero_references])
    tokens = set_actor_context("alice", "zotero-thread")
    try:
        messages = execute_tool_calls(
            [{"name": "search_zotero_references", "args": {"query": "消融实验"}, "id": "call-1"}],
            "zotero-run",
        )
    finally:
        reset_actor_context(tokens)
        end_run("zotero-run")
    assert len(messages) == 1
    assert "Zotero 命中：消融实验" in messages[0].content
    assert captured == [("alice-key", "111")]


def test_remote_http_mcp_url_rejected(monkeypatch):
    settings = _settings()
    settings.ZOTERO_MCP_URL = "http://external.example/mcp"
    monkeypatch.setattr(zotero_mcp, "get_settings", lambda: settings)
    try:
        zotero_mcp._configured_url()
    except RuntimeError as exc:
        assert "HTTPS" in str(exc)
    else:
        raise AssertionError("remote HTTP URL should be rejected")


def test_agent_reference_output_stays_valid_json_when_large(monkeypatch):
    async def many_items(_query: str, _user_id: str):
        return [{"title": f"paper-{i}", "abstract": "A" * 2000} for i in range(10)]

    monkeypatch.setattr(zotero_mcp, "search_reference_items", many_items)
    result = json.loads(asyncio.run(zotero_mcp.search_references("paper", "alice")))
    assert result["truncated"] is True
    assert 1 <= len(result["items"]) < 10


@pytest.mark.asyncio
async def test_labpilot_client_to_real_mcp_server_isolates_two_users(monkeypatch):
    """跨过真实 HTTP MCP 边界，验证生产客户端和 sidecar 能协同按人取库。"""
    async def fake_get(path, api_key, params=None):
        owner = {"alice-key": "101", "bob-key": "202"}[api_key]
        if path == "/keys/current":
            return {"userID": int(owner), "access": {"user": {"library": True}}}
        assert path == f"/users/{owner}/items/top"
        return [{"key": f"ITEM{owner}", "data": {"itemType": "journalArticle", "title": f"{owner} paper"}}]

    monkeypatch.setattr(zotero_server, "_zotero_get", fake_get)
    monkeypatch.setattr(
        zotero_credentials,
        "load_credential",
        lambda user: SimpleNamespace(
            zotero_user_id="101" if user == "alice" else "202",
            api_key=f"{user}-key",
        ),
    )
    token = "integration-test-internal-token-0123456789"
    app = zotero_server.create_app(token)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(10)
    port = sock.getsockname()[1]
    running = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    task = asyncio.create_task(running.serve(sockets=[sock]))
    try:
        for _ in range(100):
            if running.started:
                break
            if task.done():
                await task
            await asyncio.sleep(0.01)
        else:
            raise RuntimeError("MCP test server did not start")
        monkeypatch.setattr(
            zotero_mcp,
            "get_settings",
            lambda: SimpleNamespace(
                ZOTERO_MCP_URL=f"http://127.0.0.1:{port}/mcp",
                ZOTERO_MCP_INTERNAL_TOKEN=token,
                ZOTERO_MCP_TIMEOUT_SECONDS=3.0,
            ),
        )
        alice, bob = await asyncio.gather(
            zotero_mcp.search_reference_items("paper", "alice"),
            zotero_mcp.search_reference_items("paper", "bob"),
        )
        assert alice[0]["title"] == "101 paper"
        assert bob[0]["title"] == "202 paper"
    finally:
        running.should_exit = True
        await task
