"""Real configured embedding/model acceptance with isolated storage and mock mail."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
root = Path(tempfile.mkdtemp(prefix="labpilot_upload_live_"))
os.environ["DATABASE_URL"] = f"sqlite:///{root / 'business.db'}"
os.environ["CHECKPOINT_DB_PATH"] = str(root / "checkpoint.db")
os.environ["MAIL_BACKEND"] = "mock"

from fastapi.testclient import TestClient

from app.main import app
from app.services import rag

calls = []
vector_calls = []
original_search = rag.search_knowledge
original_vector_search = rag._vector_search

def traced_vector(*args, **kwargs):
    vector_calls.append(True)
    return original_vector_search(*args, **kwargs)

rag._vector_search = traced_vector

async def traced_search(query, user_id="dev", document_id=None):
    calls.append({"query": query, "user_id": user_id, "document_id": document_id})
    return await original_search(query, user_id=user_id, document_id=document_id)

rag.search_knowledge = traced_search
results = {"isolated_storage": True, "real_embedding": True, "real_model": True, "real_email_sent": False}
started = time.monotonic()
try:
    with TestClient(app) as client:
        for tid, name, body in [
            ("live-upload-other", "另一份实验.md", "星舟实验：准确率12.3%，实验代号OLD-123，截止2030-03-02。"),
            ("live-upload-target", "星舟实验记录.md", "星舟实验：准确率94.6%，实验代号LP-946-X7，截止2030-06-18。"),
        ]:
            upload = client.post("/api/v1/docs/upload", params={"session_id": tid}, files={"file": (name, body.encode(), "text/markdown")})
            assert upload.status_code == 200, f"Upload failed: HTTP {upload.status_code}"
            assert upload.json()["chunks"] > 0
        doc_id = upload.json()["document_id"]
        response = client.post("/api/v1/chat/stream", json={
            "session_id": "live-upload-target",
            "messages": [{"role": "user", "content": "请查刚上传的星舟实验记录，告诉我星舟实验的准确率、实验代号和截止日期。必须以该文件为准，引用来源。"}],
        })
        assert response.status_code == 200
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        errors = [event for event in events if event.get("type") == "error"]
        assert not errors, f"Stream error: {errors}"
        done = next(event["data"] for event in events if event["type"] == "done")
        reply = done["reply"]
        assert "94.6" in reply and "LP-946-X7" in reply and "2030" in reply, "Reply does not contain uploaded facts"
        assert "12.3%" not in reply and "OLD-123" not in reply, "Reply mixed another document"
        assert calls and all(c["document_id"] == doc_id and c["user_id"] == "dev" for c in calls), "Search was not scoped to uploaded document"
        history = client.get("/api/v1/chat/history", params={"session_id": "live-upload-target"}).json()
        assert any(m["content"] == reply for m in history["messages"]), "Reply was not persisted"
        assert vector_calls, "Vector retrieval did not execute"
        results.update(passed=True, reply=reply, search_calls=calls, vector_retrieval_executed=True, delta_count=sum(e["type"] == "delta" for e in events), history_persisted=True)
except Exception as exc:
    results.update(passed=False, error=str(exc))
    raise
finally:
    results["elapsed_seconds"] = round(time.monotonic() - started, 2)
    output = Path(__file__).resolve().parents[1] / "docs/test-results/chat-upload"
    output.mkdir(parents=True, exist_ok=True)
    (output / "live-model.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False), flush=True)
