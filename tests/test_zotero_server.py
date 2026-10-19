"""Exercise the project-owned Zotero sidecar over real MCP HTTP requests."""

import asyncio
import socket
from contextlib import asynccontextmanager

import httpx
import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from app.zotero_server import server as zotero_server

INTERNAL_TOKEN = "labpilot-internal-token-for-tests-123456789"


def _headers(api_key: str, user_id: str = "") -> dict[str, str]:
    headers = {
        "X-LabPilot-MCP-Token": INTERNAL_TOKEN,
        "X-Zotero-Api-Key": api_key,
    }
    if user_id:
        headers["X-Zotero-User-Id"] = user_id
    return headers


@asynccontextmanager
async def _live_server(app):
    # Real loopback transport: ASGITransport cannot complete MCP's SSE listen
    # request, so it would hang without exercising request headers at all.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(10)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        for _ in range(100):
            if server.started:
                break
            if task.done():
                await task
            await asyncio.sleep(0.01)
        else:
            raise RuntimeError("MCP test server did not start")
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task


async def _call_tool(base_url: str, headers: dict[str, str], name: str, arguments: dict):
    async with (
        httpx2.AsyncClient(base_url=base_url, headers=headers) as http_client,
        Client(
            streamable_http_client(f"{base_url}/mcp", http_client=http_client),
            read_timeout_seconds=3,
        ) as mcp_client,
    ):
        return await mcp_client.call_tool(name, arguments)


@pytest.mark.asyncio
async def test_zotero_server_concurrent_users_isolated(monkeypatch):
    seen: list[tuple[str, str]] = []

    async def fake_get(path, api_key, params=None):
        await asyncio.sleep(0.01)
        seen.append((path, api_key))
        user_id = {"alice-key": 101, "bob-key": 202}[api_key]
        if path == "/keys/current":
            return {"userID": user_id, "access": {"user": {"library": True}}}
        assert path == f"/users/{user_id}/items/top"
        assert params == {"q": "消融实验", "limit": 10, "format": "json"}
        return [{
            "key": f"ITEM{user_id}",
            "data": {
                "itemType": "journalArticle",
                "title": f"{user_id} 的消融实验",
                "creators": [{"firstName": "Ada", "lastName": "Lovelace"}],
                "date": "2026-10-13",
                "publicationTitle": "Lab Journal",
                "DOI": f"10.1234/{user_id}",
                "abstractNote": "可检索摘要",
                "tags": [{"tag": "实验"}],
            },
        }]

    monkeypatch.setattr(zotero_server, "_zotero_get", fake_get)
    app = zotero_server.create_app(INTERNAL_TOKEN)
    async with _live_server(app) as base_url:
        alice, bob = await asyncio.gather(
            _call_tool(base_url, _headers("alice-key", "101"), "zotero_search_items", {"query": "消融实验"}),
            _call_tool(base_url, _headers("bob-key", "202"), "zotero_search_items", {"query": "消融实验"}),
        )
    alice_text = str(alice.structured_content or alice.content)
    bob_text = str(bob.structured_content or bob.content)
    assert "101 的消融实验" in alice_text
    assert "202 的消融实验" not in alice_text
    assert "202 的消融实验" in bob_text
    assert "101 的消融实验" not in bob_text
    assert "alice-key" not in alice_text
    assert "bob-key" not in bob_text
    assert seen.count(("/keys/current", "alice-key")) == 1
    assert seen.count(("/keys/current", "bob-key")) == 1


@pytest.mark.asyncio
async def test_zotero_server_binding_info_and_mismatched_identity(monkeypatch):
    seen = []

    async def fake_get(path, api_key, params=None):
        seen.append(path)
        return {"userID": 101, "access": {"user": {"library": True}}}

    monkeypatch.setattr(zotero_server, "_zotero_get", fake_get)
    app = zotero_server.create_app(INTERNAL_TOKEN)
    async with _live_server(app) as base_url:
        info = await _call_tool(base_url, _headers("alice-key"), "zotero_connection_info", {})
        mismatch = await _call_tool(
            base_url, _headers("alice-key", "202"), "zotero_search_items", {"query": "消融实验"}
        )
    assert info.structured_content == {"user_id": "101", "access": {"library": True}}
    assert mismatch.is_error is True
    assert "不匹配" in str(mismatch.content)
    assert seen == ["/keys/current", "/keys/current"]


@pytest.mark.asyncio
async def test_zotero_server_rejects_missing_internal_token():
    app = zotero_server.create_app(INTERNAL_TOKEN)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://testserver"
    ) as http_client:
        response = await http_client.post("/mcp", json={})
    assert response.status_code == 401
    assert "Unauthorized" in response.text
    with pytest.raises(ValueError, match="at least 32"):
        zotero_server.create_app("")


@pytest.mark.asyncio
async def test_zotero_server_exposes_only_read_tools():
    app = zotero_server.create_app(INTERNAL_TOKEN)
    async with (
        _live_server(app) as base_url,
        httpx2.AsyncClient(base_url=base_url, headers=_headers("test-key")) as http_client,
        Client(streamable_http_client(f"{base_url}/mcp", http_client=http_client)) as client,
    ):
        tools = await client.list_tools()
    assert {tool.name for tool in tools.tools} == {"zotero_connection_info", "zotero_search_items"}


@pytest.mark.asyncio
async def test_zotero_server_rejects_key_without_library_read(monkeypatch):
    async def fake_get(path, api_key, params=None):
        return {"userID": 101, "access": {"user": {"library": False}}}

    monkeypatch.setattr(zotero_server, "_zotero_get", fake_get)
    app = zotero_server.create_app(INTERNAL_TOKEN)
    async with _live_server(app) as base_url:
        result = await _call_tool(base_url, _headers("restricted-key"), "zotero_connection_info", {})
    assert result.is_error is True
    assert "读取权限" in str(result.content)
    assert "restricted-key" not in str(result.content)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "headers", "expected_wait"),
    [
        (429, {"Retry-After": "17"}, "17 秒"),
        (200, {"Backoff": "9"}, "9 秒"),
    ],
)
async def test_zotero_server_honors_rate_limit_headers_without_leaking_key(
    monkeypatch, status, headers, expected_wait
):
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["Zotero-API-Key"] == "private-test-key"
        assert request.headers["Zotero-API-Version"] == "3"
        return httpx.Response(status, headers=headers, json={"detail": "private-test-key"})

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(respond)

    def mock_client(*args, **kwargs):
        return original_client(*args, transport=transport, **kwargs)

    monkeypatch.setattr(zotero_server.httpx, "AsyncClient", mock_client)
    with pytest.raises(zotero_server.ZoteroServiceError) as error:
        await zotero_server._zotero_get("/keys/current", "private-test-key")
    assert expected_wait in str(error.value)
    assert "private-test-key" not in str(error.value)
