"""RAG 三态硬门禁：有答案 / 无答案 / 数值冲突 / 检索无关判 no_result。

在离线确定性路径下验证 search_knowledge 对外契约（monkeypatch 检索与重排），
不依赖真实 embedding/rerank API，保证「指标可复现」。
"""


from app.services import rag as rag_svc


def _chunk(chunk_id: str, content: str, doc_id: str = "doc1", score: float = 0.8):
    return {
        "chunk_id": chunk_id,
        "document_id": doc_id,
        "content": content,
        "page_or_section": "chunk-0",
        "seq": 0,
        "score": score,
    }


def _patch_search(monkeypatch, hits):
    """把 async hybrid_search / rerank 替换为返回固定结果的协程。"""
    async def _hybrid(*a, **k):
        return hits

    async def _rerank(*a, **k):
        return hits

    monkeypatch.setattr(rag_svc, "hybrid_search", _hybrid)
    monkeypatch.setattr(rag_svc, "rerank", _rerank)


def test_rag_has_answer(monkeypatch):
    """有答案：检索到相关片段 → status ok + citations 非空。"""
    hits = [_chunk("c1", "消融实验显示模型在 ETTm2 上 MSE 降至 0.45。")]
    _patch_search(monkeypatch, hits)

    r = rag_svc.search_knowledge_sync("消融实验 MSE", user_id="dev")
    assert r["status"] == "ok"
    assert r["citations"]
    assert "MSE" in r["citations"][0]["quote"] or "消融" in r["citations"][0]["quote"]
    assert r["citations"][0]["chunk_id"] == "c1"


def test_rag_no_result_when_no_evidence(monkeypatch):
    """无答案：检索无候选 → status no_result + 空 citations（不编造）。"""
    _patch_search(monkeypatch, [])

    r = rag_svc.search_knowledge_sync("资料里根本没有的问题", user_id="dev")
    assert r["status"] == "no_result"
    assert r["citations"] == []
    assert "没有" in r["answer_hint"] or "未覆盖" in r["answer_hint"]


def test_rag_no_result_when_retrieved_irrelevant(monkeypatch):
    """检索到但片段与查询无共同实词 → 判无关，no_result（补救向量噪声）。"""
    hits = [_chunk("c2", "今天天气晴朗适合去郊游。", score=0.3)]
    _patch_search(monkeypatch, hits)

    r = rag_svc.search_knowledge_sync("消融实验 MSE 指标", user_id="dev")
    assert r["status"] == "no_result"
    assert r["citations"] == []


def test_rag_conflicting_values_presented_both(monkeypatch):
    """数值冲突：两份材料给出不同数值 → 并列引用（都进 citations，不擅自二选一）。"""
    hits = [
        _chunk("cA", "2026 年实验一记录准确率 90%。", doc_id="docA"),
        _chunk("cB", "2026 年实验二记录准确率 85%。", doc_id="docB"),
    ]
    _patch_search(monkeypatch, hits)

    r = rag_svc.search_knowledge_sync("准确率 实验", user_id="dev")
    assert r["status"] == "ok"
    quotes = [c["quote"] for c in r["citations"]]
    assert any("90%" in q for q in quotes)
    assert any("85%" in q for q in quotes)
    # 冲突不预设唯一答案：两个来源都进入引用
    assert len(r["citations"]) == 2
    assert {c["document_id"] for c in r["citations"]} == {"docA", "docB"}
