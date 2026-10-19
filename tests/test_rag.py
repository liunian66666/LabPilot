"""RAG：切分、关键词召回（不依赖 embedding）。"""

from app.models.db import session_scope
from app.models.tables import Chunk, Document
from app.services import rag as rag_svc


def test_split_text_overlap():
    text = "组会讨论了消融实验设计。" * 40
    chunks = rag_svc.split_text(text)
    assert len(chunks) >= 1
    assert all(c.strip() for c in chunks)


def test_keyword_search_no_embedding():
    with session_scope() as s:
        doc = Document(title="note", filename="note.md", doc_type="meeting_note")
        s.add(doc)
        s.flush()
        s.add(
            Chunk(
                document_id=doc.id,
                content="下周五前补完消融实验，结果给老师看一下。",
                seq=0,
                embedding_json=None,
            )
        )
        s.add(
            Chunk(
                document_id=doc.id,
                content="完全无关的天气内容。",
                seq=1,
                embedding_json=None,
            )
        )
    hits = rag_svc._keyword_search("消融实验", top_k=5)
    assert hits
    assert "消融" in hits[0]["content"]
    assert hits[0]["document_id"]
