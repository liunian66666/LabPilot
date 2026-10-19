"""Embedding IO must work across ASGI and short-lived Agent tool loops."""
import asyncio
import threading

from app.core import llm
from app.services import rag


def test_embedding_uses_shared_sync_client_off_loop(monkeypatch):
    loop_thread = threading.get_ident()
    calls = []

    class Embeddings:
        def embed_query(self, text):
            calls.append((text, threading.get_ident()))
            return [1.0, 0.0]

        async def aembed_query(self, text):
            raise AssertionError("Shared async client must not cross event loops")

    client = Embeddings()
    monkeypatch.setattr(llm, "get_embeddings", lambda: client)
    assert asyncio.run(rag.get_embedding("upload")) == [1.0, 0.0]
    assert asyncio.run(rag.get_embedding("agent query")) == [1.0, 0.0]
    assert [text for text, _ in calls] == ["upload", "agent query"]
    assert all(tid != loop_thread for _, tid in calls)


def test_embed_many_concurrent_and_respects_limit(monkeypatch):
    """批量 embedding：全部返回、顺序稳定、并发峰值受上限约束。"""
    texts = [f"chunk-{i}" for i in range(5)]
    in_flight = 0
    peak = 0
    lock = threading.Lock()
    seen = []

    class Embeddings:
        def embed_query(self, text):
            nonlocal in_flight, peak
            with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            # 模拟 IO 等待，制造并发窗口
            import time

            time.sleep(0.02)
            with lock:
                seen.append(text)
                in_flight -= 1
            return [float(text.split("-")[1]), 0.0]

    client = Embeddings()
    monkeypatch.setattr(llm, "get_embeddings", lambda: client)
    out = asyncio.run(rag._embed_many(texts, concurrency=3))
    assert len(out) == 5
    # 返回顺序与输入一致（gather 保持序）
    assert out[0] == [0.0, 0.0]
    assert out[4] == [4.0, 0.0]
    # 并发峰值不超过上限；且 5 块全部实际完成
    assert peak <= 3, f"peak concurrency {peak} exceeded limit"
    assert sorted(seen) == sorted(texts)


def test_embed_many_uses_default_concurrency_setting(monkeypatch):
    from app.core import config as cfg

    called = {"peak": 0, "in_flight": 0}

    class Embeddings:
        def embed_query(self, text):
            import time

            called["in_flight"] += 1
            called["peak"] = max(called["peak"], called["in_flight"])
            time.sleep(0.01)
            called["in_flight"] -= 1
            return [1.0]

    monkeypatch.setattr(llm, "get_embeddings", lambda: Embeddings())
    # _embed_many 用 get_settings().RAG_EMBED_CONCURRENCY；patch 整个函数返回带小上限的配置
    monkeypatch.setattr(
        cfg, "get_settings", lambda: type("S", (), {"RAG_EMBED_CONCURRENCY": 2})()
    )
    asyncio.run(rag._embed_many(["a", "b", "c", "d"]))
    assert called["peak"] <= 2
