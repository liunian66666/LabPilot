"""实际服务/图/checkpoint 联动，模型与 SMTP 为可控替身。"""

import sqlite3
from pathlib import Path
from uuid import UUID, uuid4

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from sqlmodel import select

from app.agent import graph
from app.core.config import get_settings
from app.models.db import session_scope
from app.models.tables import Approval


def test_report_edit_approval_reload_and_execute_once(client, monkeypatch, tmp_path):
    from app.api.v1 import router

    extraction = client.post(
        "/api/v1/notes/extract", json={"text": "本周五前完成消融实验", "persist": True}
    )
    assert extraction.status_code == 200
    assert extraction.json()["tasks"]
    todo_id = extraction.json()["tasks"][0]["todo_id"]
    assert (
        client.post(
            "/api/v1/progress",
            json={"work_done": "消融实验准确率78.3%", "todo_id": todo_id},
        ).status_code
        == 200
    )
    report = client.post("/api/v1/reports/weekly", json={}).json()
    current = client.get(f"/api/v1/reports/{report['id']}").json()
    full_text = "修改后全文：消融实验准确率78.3%。\n" + "完整证据段落。" * 300
    edit = client.patch(
        f"/api/v1/reports/{report['id']}",
        json={"body_markdown": full_text, "expected_hash": current["content_hash"]},
    )
    assert edit.status_code == 200
    assert (
        client.patch(
            f"/api/v1/reports/{report['id']}",
            json={
                "body_markdown": "错误覆盖",
                "expected_hash": current["content_hash"],
            },
        ).status_code
        == 409
    )

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    model = Model(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "prepare_email_tool",
                        "id": "prepare",
                        "args": {
                            "report_id": report["id"],
                            "to_email": "teacher@example.com",
                            "subject": "修改后周报",
                        },
                    }
                ],
            ),
            AIMessage(content="审批结果已保存。"),
        ]
    )
    monkeypatch.setattr(graph, "get_chat_model", lambda: model)
    monkeypatch.setenv("CHECKPOINT_DB_PATH", str(tmp_path / "checkpoints.db"))
    get_settings.cache_clear()
    first = graph.LabPilotAgent()
    monkeypatch.setattr(router, "agent", first)
    tid = uuid4().hex
    try:
        response = client.post(
            "/api/v1/chat",
            json={
                "session_id": tid,
                "messages": [{"role": "user", "content": "用修改后的周报拟信"}],
            },
        )
        assert response.status_code == 200, response.text
        approval_id = response.json()["pending_approval_id"]
        assert approval_id
    finally:
        first._saver_cm.__exit__(None, None, None)

    # 关闭连接后新建 Agent，实际从 SQLite 重新读取暂停的图。
    second = graph.LabPilotAgent()
    monkeypatch.setattr(router, "agent", second)
    try:
        history = client.get("/api/v1/chat/history", params={"session_id": tid}).json()
        assert history["pending_approval_id"] == approval_id
        approval = client.get(f"/api/v1/approvals/{approval_id}").json()
        assert approval["payload"]["body"] == full_text
        decision = {
            "approval_id": approval_id,
            "payload_version": approval["payload_version"],
            "decision": "approved",
            "session_id": tid,
        }
        accepted = client.post("/api/v1/chat/resume", json=decision)
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["status"] == "sent"
        assert accepted.json()["graph_resume"] == "图已恢复。"
        assert client.post("/api/v1/chat/resume", json=decision).json()["idempotent"]
        with session_scope() as session:
            rows = session.exec(
                select(Approval).where(Approval.session_id == tid)
            ).all()
            assert len(rows) == 1
            assert session.get(Approval, UUID(approval_id)).status == "sent"
        assert not second._graph.get_state(second._config(tid, "dev")).next
    finally:
        second._saver_cm.__exit__(None, None, None)


def test_sqlite_backup_restores_business_and_checkpoint(tmp_path):
    from scripts.sqlite_snapshot import snapshot

    business, checkpoint = tmp_path / "business.db", tmp_path / "checkpoint.db"
    for path in (business, checkpoint):
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE evidence (value TEXT)")
            connection.execute("INSERT INTO evidence VALUES ('preserved')")
    destination = tmp_path / "backup"
    manifest = snapshot(business, checkpoint, destination)
    assert len(manifest["files"]) == 2
    for name in manifest["files"]:
        with sqlite3.connect(destination / name) as restored:
            assert (
                restored.execute("SELECT value FROM evidence").fetchone()[0]
                == "preserved"
            )
            assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert Path(destination / "manifest.json").is_file()
