"""文档重命名：PUT /docs/{id} 的归属校验、标题校验与审计。"""

from sqlmodel import select

from app.models.db import session_scope
from app.models.tables import AuditEvent, Document


def _make_doc(*, title: str = "旧标题", user_id: str = "dev", filename: str = "old.md") -> str:
    with session_scope() as s:
        d = Document(title=title, filename=filename, user_id=user_id, doc_type="paper")
        s.add(d)
        s.flush()
        return str(d.id)


def _audit_events() -> list[str]:
    with session_scope() as s:
        return [e.event_type for e in s.exec(select(AuditEvent)).all()]


def test_rename_doc_ok(client):
    doc_id = _make_doc()
    r = client.put(f"/api/v1/docs/{doc_id}", json={"title": "新标题"})
    assert r.status_code == 200
    assert r.json()["title"] == "新标题"
    with session_scope() as s:
        d = s.get(Document, __import__("uuid").UUID(doc_id))
        assert d.title == "新标题"
    assert "doc_renamed" in _audit_events()


def _count_renamed() -> int:
    """统计 doc_renamed 审计事件数（用于比较增量，避免跨测试累积干扰）。"""
    with session_scope() as s:
        return len(
            s.exec(select(AuditEvent).where(AuditEvent.event_type == "doc_renamed")).all()
        )


def test_rename_doc_blank_title_422(client):
    doc_id = _make_doc()
    before = _count_renamed()
    # 空字符串
    r1 = client.put(f"/api/v1/docs/{doc_id}", json={"title": ""})
    assert r1.status_code == 422
    # 全空格
    r2 = client.put(f"/api/v1/docs/{doc_id}", json={"title": "   "})
    assert r2.status_code == 422
    # 缺 title 键
    r3 = client.put(f"/api/v1/docs/{doc_id}", json={})
    assert r3.status_code == 422
    # 均未改动、无新增审计
    with session_scope() as s:
        d = s.get(Document, __import__("uuid").UUID(doc_id))
        assert d.title == "旧标题"
    assert _count_renamed() == before


def test_rename_doc_not_found_404(client):
    import uuid

    r = client.put(f"/api/v1/docs/{uuid.uuid4()}", json={"title": "x"})
    assert r.status_code == 404


def test_rename_doc_forbidden_other_user_404(client):
    doc_id = _make_doc(user_id="other")
    r = client.put(f"/api/v1/docs/{doc_id}", json={"title": "入侵"})
    assert r.status_code == 404
    with session_scope() as s:
        d = s.get(Document, __import__("uuid").UUID(doc_id))
        assert d.title == "旧标题"
        assert d.user_id == "other"


def test_rename_doc_invalid_id_400(client):
    r = client.put("/api/v1/docs/not-a-uuid", json={"title": "x"})
    assert r.status_code == 400
