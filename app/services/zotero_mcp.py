"""LabPilot 的 Zotero MCP 客户端：每次请求只使用当前用户的凭证。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from app.core.config import get_settings
from app.core.logging import log_event

SEARCH_TOOL = "zotero_search_items"
IDENTITY_TOOL = "zotero_connection_info"
MAX_QUERY_LENGTH = 200
MAX_RESULT_LENGTH = 6000

# 口语化冗余词：用户输入自然语言时先过滤掉这些无意义的引导词，只留核心检索词
_STOP_WORDS = {
    "给我找", "帮我找", "帮我搜", "找一下", "找一找", "搜索", "检索",
    "相关的", "相关", "文献", "论文", "资料", "我要", "我想", "请问",
    "一下", "所有", "全部", "帮我", "给我", "关于", "的", "和", "与",
}


def _clean_query(raw: str) -> str:
    """把用户口语化的自然语言query清洗成干净的检索关键词。"""
    q = raw.strip()
    for w in _STOP_WORDS:
        q = q.replace(w, "")
    # 合并多余空格、标点
    q = q.strip(" ，。！？、：；,.!?;:-—~")
    return q or raw.strip()


async def _expand_bilingual_query(clean_q: str) -> str:
    """把中文科研关键词自动翻译成学术英文，双语一起搜，解决中文搜不到英文文献的问题。"""
    # 纯英文query不需要翻译，直接返回
    if all(ord(c) < 128 for c in clean_q):
        return ""
    from app.agent.budget import consume

    consume("llm")
    try:
        from app.core.llm import get_chat_model
        # 用现有LLM做术语翻译，只返回学术英文关键词
        translated = await asyncio.to_thread(
            lambda: get_chat_model().invoke(
                f"把下面的科研文献检索关键词翻译成对应的学术英文核心术语，只输出1-2个最核心的英文单词/短语，不要句子、不要解释、不要多余词：{clean_q}"
            ).content.strip()
        )
        # 去掉翻译结果里可能的多余符号，只保留前两个核心词，避免多词AND匹配搜不到
        translated = translated.strip("。.!?，,；;：:").split()[:2]
        translated = " ".join(translated)
        log_event("zotero_translate_success", query=clean_q, translated=translated)
        return translated
    except Exception as e:  # noqa: BLE001 - 翻译失败回退原文检索
        # 翻译失败打日志，返回空字符串，跳过英文搜索
        log_event("zotero_translate_failed", query=clean_q, error=str(e))
        return ""


def _configured_url() -> str:
    settings = get_settings()
    url = settings.ZOTERO_MCP_URL.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise RuntimeError("Zotero MCP 地址配置无效。")
    if parsed.scheme == "http" and parsed.hostname not in {
        "localhost", "127.0.0.1", "zotero-mcp"
    }:
        raise RuntimeError("远程 Zotero MCP 连接必须使用 HTTPS。")
    if not settings.ZOTERO_MCP_INTERNAL_TOKEN.strip():
        raise RuntimeError("Zotero MCP 内部访问密钥未配置。")
    return url


def enabled_for(user_id: str) -> bool:
    """绑定凭证且 MCP 已配置的用户才能向 Agent 暴露检索工具。"""
    settings = get_settings()
    if not settings.ZOTERO_MCP_URL.strip() or not settings.ZOTERO_MCP_INTERNAL_TOKEN.strip():
        return False
    from app.services.zotero_credentials import has_credential

    return has_credential(user_id)


@asynccontextmanager
async def _client(api_key: str, zotero_user_id: str = "") -> AsyncIterator[Client]:
    settings = get_settings()
    url = _configured_url()
    headers = {
        "X-LabPilot-MCP-Token": settings.ZOTERO_MCP_INTERNAL_TOKEN,
        "X-Zotero-Api-Key": api_key,
    }
    if zotero_user_id:
        headers["X-Zotero-User-Id"] = zotero_user_id
    # MCP 2.x 的 Client(url, headers=...) 不存在；自定义头放在 HTTP transport。
    async with httpx2.AsyncClient(headers=headers, timeout=settings.ZOTERO_MCP_TIMEOUT_SECONDS) as http:
        transport = streamable_http_client(url, http_client=http)
        async with Client(transport, read_timeout_seconds=settings.ZOTERO_MCP_TIMEOUT_SECONDS) as client:
            yield client


def _result_payload(result: Any) -> dict[str, Any]:
    payload = result.structured_content
    if isinstance(payload, dict):
        return payload
    text = "\n".join(
        block.text for block in result.content if getattr(block, "type", None) == "text"
    )
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError("Zotero MCP 返回的数据格式无效。") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("Zotero MCP 返回的数据格式无效。")  # noqa: TRY004
    return parsed


async def verify_zotero_credentials(api_key: str) -> dict[str, Any]:
    """通过 MCP 验证用户的 Zotero Key；LabPilot 不直接访问 Zotero REST。"""
    if not api_key or len(api_key) > 512:
        raise ValueError("请输入有效的 Zotero API Key。")
    try:
        async with _client(api_key) as client:
            result = await client.call_tool(IDENTITY_TOOL, {})
    except Exception as exc:  # noqa: BLE001 - 不回显可能含凭证的远端异常
        log_event("zotero_mcp_verify_unavailable", error=type(exc).__name__)
        raise RuntimeError("Zotero MCP 暂时不可用。") from None
    if result.is_error:
        raise ValueError("Zotero API Key 无效或没有文献库读取权限。")
    payload = _result_payload(result)
    user_id = str(payload.get("user_id") or "")
    if not user_id.isdigit():
        raise ValueError("Zotero MCP 未返回有效的用户 ID。")
    return {"user_id": user_id}


async def search_reference_items(query: str, user_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """按当前 LabPilot 用户的凭证调用只读 MCP 检索工具。"""
    raw_query = query.strip()
    if not raw_query or len(raw_query) > MAX_QUERY_LENGTH:
        raise ValueError(f"检索词应为 1–{MAX_QUERY_LENGTH} 个字符。")
    if not 1 <= limit <= 50:
        raise ValueError("检索数量应为 1–50 条。")

    from app.services.zotero_credentials import load_credential

    credential = load_credential(user_id)
    if credential is None:
        raise ValueError("请先在设置中连接 Zotero。")
    # 清洗口语化query：去掉"给我找""相关文献"这类冗余词，只留核心关键词
    clean_q = _clean_query(raw_query)
    # 自动双语增强：把中文关键词翻译成学术英文，分开两次搜索（避免Zotero的AND匹配逻辑）
    translated_q = await _expand_bilingual_query(clean_q)
    search_queries = list(dict.fromkeys([q for q in [translated_q] if q and q != clean_q]))
    all_items = []
    seen_keys = set()
    result_cn = None
    try:
        async with _client(credential.api_key, credential.zotero_user_id) as client:
            # 第一次搜清洗后的中文核心词
            result_cn = await client.call_tool(SEARCH_TOOL, {"query": clean_q, "limit": limit})
            payload_cn = _result_payload(result_cn)
            for item in payload_cn.get("items", []):
                if isinstance(item, dict):
                    key = str(item.get("zotero_key") or item.get("title", ""))
                    if key not in seen_keys:
                        seen_keys.add(key)
                        all_items.append(item)
            # 第二次搜翻译后的英文关键词
            for q in search_queries:
                log_event("zotero_en_search_start", query=q)
                result_en = await client.call_tool(SEARCH_TOOL, {"query": q, "limit": limit})
                payload_en = _result_payload(result_en)
                en_items = payload_en.get("items", [])
                log_event("zotero_en_search_done", query=q, got_count=len(en_items))
                for item in en_items:
                    if isinstance(item, dict):
                        key = str(item.get("zotero_key") or item.get("title", ""))
                        if key not in seen_keys:
                            seen_keys.add(key)
                            all_items.append(item)
    except Exception as exc:  # noqa: BLE001 - 不回显可能含凭证的远端异常
        log_event("zotero_mcp_search_unavailable", error=type(exc).__name__)
        raise RuntimeError("Zotero MCP 暂时不可用。") from None
    if result_cn and result_cn.is_error:
        raise RuntimeError("Zotero 检索失败，请检查文献库连接。")
    return [item for item in all_items[:limit] if isinstance(item, dict)]


async def search_references(query: str, user_id: str) -> str:
    """Agent 入口：把预期的集成故障转成可读回复，不中断主对话。"""
    try:
        items = await search_reference_items(query, user_id)
    except (ValueError, RuntimeError) as exc:
        return str(exc)
    if not items:
        return "Zotero 文献库未返回匹配条目。"
    selected: list[dict[str, Any]] = []
    for item in items:
        candidate = json.dumps({"items": [*selected, item]}, ensure_ascii=False)
        if len(candidate) > MAX_RESULT_LENGTH:
            if not selected:
                # 一条极长摘要也不能使返回值变成截断的、无效的 JSON。
                selected.append(
                    {
                        "zotero_key": str(item.get("zotero_key", ""))[:40],
                        "title": str(item.get("title", ""))[:300],
                        "doi": str(item.get("doi", ""))[:200],
                    }
                )
            break
        selected.append(item)
    return json.dumps({"items": selected, "truncated": len(selected) < len(items)}, ensure_ascii=False)
