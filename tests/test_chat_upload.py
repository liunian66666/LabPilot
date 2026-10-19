"""Chat uploads share ingestion and persist searchable document receipts."""
import asyncio
import io
from uuid import uuid4

import pytest
from fastapi import HTTPException, UploadFile

from app.api.v1.router import upload_doc
from app.services import chat_store, rag


def test_chat_upload_indexes_file_and_saves_receipt(monkeypatch):
    async def embed(text):
        return [1.0, 0.0]

    monkeypatch.setattr(rag, "get_embedding", embed)
    tid = f"upload-{uuid4().hex}"
    name = f"experiment-{uuid4().hex}.md"
    result = asyncio.run(upload_doc(
        UploadFile(filename=name, file=io.BytesIO("消融实验唯一结论：准确率91.7%。".encode())),
        doc_type="other", session_id=tid, current_user="dev",
    ))
    assert result["document_id"] and result["chunks"] > 0
    messages = chat_store.list_messages(tid)
    assert messages[0]["card"]["type"] == "document"
    assert messages[0]["card"]["title"] == name
    hits = rag._keyword_search("消融实验", 5, "dev", result["document_id"])
    assert hits and all(h["document_id"] == result["document_id"] for h in hits)
    assert "91.7%" in hits[0]["content"]
    assert not rag._keyword_search("消融实验", 5, "another-user", result["document_id"])


@pytest.mark.parametrize("filename,content,status", [
    ("bad.exe", b"data", 422), ("empty.txt", b"", 422),
    ("blank.md", b" \n ", 422), ("invalid.txt", b"\xff\xfe", 422),
    ("big.txt", b"a" * (20 * 1024 * 1024 + 1), 413),
], ids=["unsupported", "empty", "blank", "invalid-encoding", "oversize"])
def test_upload_rejects_invalid_file_without_success_card(filename, content, status):
    tid = uuid4().hex
    with pytest.raises(HTTPException) as err:
        asyncio.run(upload_doc(UploadFile(filename=filename, file=io.BytesIO(content)), session_id=tid, current_user="dev"))
    assert err.value.status_code == status
    assert not chat_store.list_messages(tid)


def test_upload_embedding_failure_does_not_claim_success(monkeypatch):
    async def embed(text):
        raise RuntimeError("embedding service unavailable")

    monkeypatch.setattr(rag, "get_embedding", embed)
    tid = uuid4().hex
    with pytest.raises(HTTPException) as err:
        asyncio.run(upload_doc(UploadFile(filename="valid.txt", file=io.BytesIO(b"content")), session_id=tid, current_user="dev"))
    assert err.value.status_code == 503
    assert not chat_store.list_messages(tid)


def test_chat_upload_extracts_text_pdf(monkeypatch):
    import fitz

    async def embed(text):
        return [1.0, 0.0]

    monkeypatch.setattr(rag, "get_embedding", embed)
    with fitz.open() as pdf:
        pdf.new_page().insert_text((72, 72), "PDF upload acceptance: accuracy 91.7%")
        content = pdf.tobytes()
    tid = uuid4().hex
    result = asyncio.run(upload_doc(
        UploadFile(filename="experiment.pdf", file=io.BytesIO(content)), session_id=tid, current_user="dev",
    ))
    hits = rag._keyword_search("acceptance", 5, "dev", result["document_id"])
    assert hits and "91.7%" in hits[0]["content"]
    assert chat_store.list_messages(tid)[0]["card"]["type"] == "document"


def test_uploaded_document_context_passed_to_agent():
    from app.schemas import ChatMessage, ChatRequest
    from app.services.chat_stream import stream_chat_events

    tid = uuid4().hex
    doc_id = uuid4().hex
    chat_store.append_message(tid, "user", "之前的提问")
    chat_store.append_message(tid, "card", "paper.md", card={"type": "document", "id": doc_id, "title": "paper.md"})
    received = []

    class Stub:
        def chat_stream(self, **kwargs):
            received.append(kwargs["user_text"])
            yield {"type": "done", "data": {"reply": "测试", "artifacts": []}}

    async def collect():
        body = ChatRequest(session_id=tid, messages=[ChatMessage(role="user", content="总结刚上传的文件")])
        return [event async for event in stream_chat_events(Stub(), body, "dev")]

    asyncio.run(collect())
    assert doc_id in received[0] and "paper.md" in received[0]
