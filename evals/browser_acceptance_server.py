"""Isolated backend for browser acceptance; never sends real email."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
root = Path(tempfile.mkdtemp(prefix="labpilot_browser_"))
os.environ["DATABASE_URL"] = f"sqlite:///{root / 'business.db'}"
os.environ["CHECKPOINT_DB_PATH"] = str(root / "checkpoints.db")
os.environ["MAIL_BACKEND"] = "mock"

from app.models.db import init_db
from app.services import chat_store, mail, report

init_db()
draft = report.generate_weekly_report(user_id="dev")
info = report.get_report(str(draft.id))
for tid, title in (("browser-A", "验收会话 A"), ("browser-B", "验收会话 B")):
    chat_store.ensure_thread(tid, title=title)
    chat_store.append_message(tid, "user", f"{title}的原始提问")
    chat_store.append_message(tid, "assistant", "周报已生成，请查看卡片。")
for status in ("draft", "draft", "edited"):
    chat_store.append_message("browser-A", "card", "周报草稿", card={
        "type": "report", "id": str(draft.id), "title": "周报草稿",
        "status": status, "content_hash": info["content_hash"],
    })
for subject in ("验收拒绝邮件",):
    approval = mail.prepare_email(
        user_id="dev", session_id="browser-A", to_email="teacher@example.com",
        subject=subject, body="浏览器验收完整正文\n第二段内容，仅 mock，不真实发送。",
    )
    chat_store.append_message("browser-A", "card", subject, card={
        "type": "approval", "id": approval.id, "title": subject,
        "status": "pending", "content_hash": approval.payload_version,
    })

if __name__ == "__main__":
    import uvicorn

    if os.environ.get("BROWSER_ACCEPTANCE_FAKE_EMBEDDING") == "1":
        from app.services import rag

        async def acceptance_embedding(text):
            return [1.0, 0.0]

        rag.get_embedding = acceptance_embedding
        rag.get_settings().RAG_USE_RERANK = False

    if os.environ.get("BROWSER_ACCEPTANCE_FAKE_MODEL") == "1":
        from langchain_core.language_models.fake_chat_models import FakeListChatModel

        from app.agent import graph

        class AcceptanceModel(FakeListChatModel):
            def bind_tools(self, tools, **kwargs):
                return self

        graph.get_chat_model = lambda: AcceptanceModel(responses=["流式验收：文字正在逐段显示，回复完成。"], sleep=0.08)

    print(f"Isolated acceptance backend: {root}; MAIL_BACKEND=mock", flush=True)
    uvicorn.run("app.main:app", host="127.0.0.1", port=8019)
