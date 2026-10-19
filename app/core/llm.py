"""LLM 封装：懒加载 OpenAI 兼容客户端。"""

from functools import lru_cache
from typing import Any

from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.core.config import get_settings


class IncompleteReplyError(RuntimeError):
    """Model finished without a usable, complete response for the current turn."""


@lru_cache
def get_chat_model(**kwargs: Any) -> ChatOpenAI:
    s = get_settings()
    params: dict[str, Any] = {
        "model": s.DEFAULT_LLM_MODEL,
        "api_key": s.OPENAI_API_KEY,
        "base_url": s.OPENAI_BASE_URL,
        "temperature": s.LLM_TEMPERATURE,
        "max_tokens": s.MAX_TOKENS,
        "timeout": s.LLM_TIMEOUT_SECONDS,
        "max_retries": s.LLM_MAX_RETRIES,
    }
    params.update(kwargs)
    return ChatOpenAI(**params)


@lru_cache
def get_embeddings() -> OpenAIEmbeddings:
    s = get_settings()
    return OpenAIEmbeddings(
        model=s.EMBEDDING_MODEL,
        api_key=s.OPENAI_API_KEY or "missing",
        base_url=s.OPENAI_BASE_URL,
        check_embedding_ctx_length=False,
        request_timeout=s.LLM_TIMEOUT_SECONDS,
        max_retries=s.LLM_MAX_RETRIES,
    )


async def ainvoke(messages: list[BaseMessage], **kwargs: Any) -> BaseMessage:
    model = get_chat_model(**kwargs) if kwargs else get_chat_model()
    return await model.ainvoke(messages)
