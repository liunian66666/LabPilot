"""通过 API 使用另一用户的真实对象 ID，必须拒绝且无副作用。"""

from uuid import uuid4

from sqlmodel import select

from app.models.db import session_scope
from app.models.tables import Approval, Chunk, Document, ProgressEntry


def test_foreign_objects_cannot_be_read_mutated_or_resumed(make_client, monkeypatch):
    from app.api.v1 import router as api

    owner, stranger = "owner_" + uuid4().hex[:8], "other_" + uuid4().hex[:8]
    client = make_client(owner)
    tid = uuid4().hex
    assert client.post("/api/v1/threads", json={"thread_id": tid}).status_code == 200
    todo = client.post("/api/v1/todos", json={"title": "private"}).json()
    report = client.post("/api/v1/reports/weekly", json={}).json()
    approval = client.post(
        "/api/v1/approvals/email",
        json={
            "session_id": tid,
            "to_email": "teacher@example.com",
            "subject": "private",
            "body": "secret",
        },
    ).json()
    with session_scope() as session:
        doc = Document(user_id=owner, title="private", content_hash=uuid4().hex)
        session.add(doc)
        session.flush()
        did = str(doc.id)
        session.add(Chunk(document_id=doc.id, content="secret evidence", seq=0))

    def no_resume(**kwargs):
        raise AssertionError("Unauthorized request must not resume or execute")

    monkeypatch.setattr(api.agent, "resume", no_resume)
    client = make_client(stranger)
    for response in (
        client.get(f"/api/v1/reports/{report['id']}"),
        client.patch(
            f"/api/v1/reports/{report['id']}",
            json={"body_markdown": "stolen", "expected_hash": "fake"},
        ),
        client.patch(f"/api/v1/todos/{todo['id']}", json={"title": "stolen"}),
        client.post(
            "/api/v1/progress", json={"work_done": "forged", "todo_id": todo["id"]}
        ),
        client.get("/api/v1/chat/history", params={"session_id": tid}),
        client.patch(f"/api/v1/threads/{tid}", json={"title": "stolen"}),
        client.delete(f"/api/v1/threads/{tid}"),
        client.post(f"/api/v1/threads/{tid}/messages", json={"content": "injected"}),
        client.get(f"/api/v1/docs/{did}"),
        client.put(f"/api/v1/docs/{did}", json={"title": "stolen"}),
        client.delete(f"/api/v1/docs/{did}"),
        client.post(
            "/api/v1/docs/upload",
            params={"session_id": tid},
            files={"file": ("secret.txt", b"injected", "text/plain")},
        ),
        client.get(f"/api/v1/approvals/{approval['approval_id']}"),
        client.post(
            "/api/v1/chat/resume",
            json={
                "approval_id": approval["approval_id"],
                "decision": "approved",
                "payload_version": approval["payload_version"],
                "session_id": tid,
            },
        ),
    ):
        assert response.status_code == 404, response.text
    with session_scope() as session:
        assert not session.exec(
            select(Document).where(Document.user_id == stranger)
        ).all()
        assert not session.exec(
            select(ProgressEntry).where(ProgressEntry.user_id == stranger)
        ).all()
        from uuid import UUID

        assert session.get(Approval, UUID(approval["approval_id"])).status == "pending"
