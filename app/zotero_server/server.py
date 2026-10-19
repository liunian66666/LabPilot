"""Stateless Zotero MCP tools backed by the official Zotero Web API.

Only LabPilot's backend can reach this service in the Compose network. Each
request carries its own Zotero key; no Zotero account is configured globally.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Mapping
from typing import Any

import httpx
from mcp import types
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

ZOTERO_API_BASE = "https://api.zotero.org"
_MAX_QUERY_LENGTH = 200
_MAX_RESULTS = 50

server = MCPServer("LabPilot Zotero References")


class ZoteroServiceError(ToolError):
    """A safe, credential-free error message for an MCP tool response."""


class InternalTokenMiddleware:
    """Reject all unauthenticated MCP HTTP requests before protocol handling."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            supplied = Headers(scope=scope).get("x-labpilot-mcp-token", "")
            if not self.token or not secrets.compare_digest(supplied.encode(), self.token.encode()):
                await PlainTextResponse("Unauthorized", status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _header(ctx: Context, name: str) -> str:
    headers = ctx.headers or {}
    # Mapping supplied by an HTTP transport can be case-sensitive; HTTP names are not.
    for header_name, value in headers.items():
        if header_name.lower() == name.lower():
            return value.strip()
    return ""


def _credentials(ctx: Context, *, require_user_id: bool) -> tuple[str, str]:
    key = _header(ctx, "X-Zotero-Api-Key")
    if not key or len(key) > 512 or "\r" in key or "\n" in key:
        raise ZoteroServiceError("缺少有效的 Zotero API Key。")
    user_id = _header(ctx, "X-Zotero-User-Id")
    if require_user_id and (not user_id.isascii() or not user_id.isdecimal() or len(user_id) > 20):
        raise ZoteroServiceError("缺少有效的 Zotero 用户 ID。")
    return key, user_id


def _retry_hint(headers: Mapping[str, str]) -> str:
    for name in ("Retry-After", "Backoff"):
        raw = headers.get(name, "").strip()
        if len(raw) <= 10 and raw.isascii() and raw.isdecimal():
            seconds = min(int(raw), 3600)
            return f"请至少等待 {seconds} 秒后重试。"
    return "请稍后重试。"


async def _zotero_get(path: str, api_key: str, params: dict[str, Any] | None = None) -> Any:
    # The base URL and path are constructed by this module, never from tool input.
    try:
        async with httpx.AsyncClient(base_url=ZOTERO_API_BASE, timeout=10.0) as client:
            response = await client.get(
                path,
                params=params,
                headers={"Zotero-API-Version": "3", "Zotero-API-Key": api_key},
            )
    except httpx.RequestError as exc:
        raise ZoteroServiceError("暂时无法连接 Zotero，请稍后重试。") from exc
    if response.status_code in (401, 403):
        raise ZoteroServiceError("Zotero API Key 无效或没有文献库读取权限。")
    if response.status_code == 429:
        raise ZoteroServiceError(f"Zotero 请求过于频繁，{_retry_hint(response.headers)}")
    if response.status_code >= 500:
        raise ZoteroServiceError(f"Zotero 服务暂时不可用，{_retry_hint(response.headers)}")
    if response.status_code != 200:
        raise ZoteroServiceError("Zotero 请求失败，请检查绑定信息。")
    # Zotero can request a Backoff on successful responses too. Stop before
    # issuing the next `/items/top` request in the same search operation.
    if response.headers.get("Backoff"):
        raise ZoteroServiceError(f"Zotero 要求暂缓后续请求，{_retry_hint(response.headers)}")
    try:
        return response.json()
    except ValueError as exc:
        raise ZoteroServiceError("Zotero 返回了无效数据。") from exc


async def _verified_owner(api_key: str) -> str:
    info = await _zotero_get("/keys/current", api_key)
    if not isinstance(info, dict):
        raise ZoteroServiceError("Zotero 返回了无效的密钥信息。")
    user_id = info.get("userID")
    user_access = info.get("access", {}).get("user", {}) if isinstance(info.get("access"), dict) else {}
    if not isinstance(user_access, dict) or user_access.get("library") is not True:
        raise ZoteroServiceError("该 Zotero API Key 没有个人文献库读取权限。")
    if isinstance(user_id, bool) or not str(user_id).isascii() or not str(user_id).isdecimal():
        raise ZoteroServiceError("Zotero 返回了无效的用户 ID。")
    return str(user_id)


def _limited_text(value: Any, length: int) -> str:
    return value[:length] if isinstance(value, str) else ""


def _normalize_item(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    data = item.get("data")
    if not isinstance(data, dict) or data.get("itemType") in ("attachment", "note"):
        return None
    creators = data.get("creators")
    authors: list[str] = []
    if isinstance(creators, list):
        for creator in creators[:10]:
            if not isinstance(creator, dict):
                continue
            name = creator.get("name") or " ".join(
                part for part in (creator.get("firstName"), creator.get("lastName")) if isinstance(part, str)
            )
            if isinstance(name, str) and name.strip():
                authors.append(name.strip()[:120])
    tags_raw = data.get("tags")
    tags = (
        [_limited_text(tag.get("tag"), 80) for tag in tags_raw[:20] if isinstance(tag, dict)]
        if isinstance(tags_raw, list)
        else []
    )
    date = _limited_text(data.get("date"), 80)
    year_match = re.search(r"(?<!\d)(?:19|20)\d{2}(?!\d)", date)
    return {
        "zotero_key": _limited_text(item.get("key") or data.get("key"), 40),
        "title": _limited_text(data.get("title"), 300),
        "authors": authors,
        "year": year_match.group(0) if year_match else "",
        "venue": _limited_text(
            data.get("publicationTitle") or data.get("proceedingsTitle") or data.get("publisher"), 200
        ),
        "doi": _limited_text(data.get("DOI") or data.get("doi"), 200),
        "abstract": _limited_text(data.get("abstractNote"), 1200),
        "url": _limited_text(data.get("url"), 500),
        "tags": [tag for tag in tags if tag],
    }


@server.tool(
    description="Verify a Zotero API Key and return its personal library user ID.",
    annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
async def zotero_connection_info(ctx: Context) -> dict[str, Any]:
    api_key, _ = _credentials(ctx, require_user_id=False)
    user_id = await _verified_owner(api_key)
    return {"user_id": user_id, "access": {"library": True}}


@server.tool(
    description="Search top-level references in the Zotero library bound to this request.",
    annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
)
async def zotero_search_items(query: str, ctx: Context, limit: int = 10) -> dict[str, Any]:
    api_key, user_id = _credentials(ctx, require_user_id=True)
    cleaned_query = query.strip()
    if not cleaned_query or len(cleaned_query) > _MAX_QUERY_LENGTH:
        raise ZoteroServiceError("检索词长度须为 1–200 个字符。")
    if isinstance(limit, bool) or not 1 <= limit <= _MAX_RESULTS:
        raise ZoteroServiceError("检索条数须为 1–50。")
    owner_id = await _verified_owner(api_key)
    if owner_id != user_id:
        raise ZoteroServiceError("Zotero 用户 ID 与 API Key 不匹配。")
    raw_items = await _zotero_get(
        f"/users/{user_id}/items/top", api_key, {"q": cleaned_query, "limit": limit, "format": "json"}
    )
    if not isinstance(raw_items, list):
        raise ZoteroServiceError("Zotero 返回了无效的文献列表。")
    return {"items": [normalized for item in raw_items if (normalized := _normalize_item(item)) is not None]}


def create_app(internal_token: str) -> ASGIApp:
    """Build the authenticated Streamable HTTP app; an empty token is invalid."""
    if len(internal_token) < 32:
        raise ValueError("ZOTERO_MCP_INTERNAL_TOKEN must contain at least 32 characters")
    mcp_app = server.streamable_http_app(
        json_response=True,
        stateless_http=True,
        host="0.0.0.0",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["zotero-mcp:8765", "localhost:*", "127.0.0.1:*", "testserver"],
            allowed_origins=[],
        ),
    )
    mcp_app.add_middleware(InternalTokenMiddleware, token=internal_token)
    return mcp_app
