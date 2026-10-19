"""History restores business state and retains user-selected thread titles."""

from datetime import datetime
from uuid import uuid4

from app.services import chat_store, mail


def test_thread_title_survives_later_messages():
    tid = f"title-{uuid4().hex}"
    chat_store.ensure_thread(tid, title="新会话")
    chat_store.ensure_thread(tid, title="第一次提问")
    chat_store.rename_thread(tid, "消融实验汇报")
    chat_store.ensure_thread(tid, title="第二次提问")
    chat_store.append_message(tid, "user", "第三次提问")
    row = next(r for r in chat_store.list_threads() if r["thread_id"] == tid)
    assert row["title"] == "消融实验汇报"


def test_history_restores_resolved_email_body_and_timestamp():
    tid = f"approval-history-{uuid4().hex}"
    approval = mail.prepare_email(
        user_id="dev", session_id=tid, to_email="teacher@example.com",
        subject="本周周报", body="完整周报正文\n第二段",
    )
    recorded = chat_store.append_message(
        tid, "card", "邮件草稿", card={
            "type": "approval", "id": approval.id,
            "status": "pending", "content_hash": approval.payload_version,
        },
    )
    mail.decide_approval(
        user_id="dev", approval_id=approval.id, session_id=tid,
        payload_version_in=approval.payload_version, decision="rejected",
    )
    history = chat_store.list_messages(tid)
    assert history[0]["card"]["status"] == "rejected"
    assert history[0]["card"]["body"] == "teacher@example.com\n完整周报正文\n第二段"
    assert datetime.fromisoformat(history[0]["created_at"]) == datetime.fromisoformat(recorded["created_at"])


def test_history_api_keeps_message_timestamp(monkeypatch):
    from app.api.v1 import router

    tid = f"history-time-{uuid4().hex}"
    row = chat_store.append_message(tid, "user", "以前的消息")
    monkeypatch.setattr(router.agent, "history", lambda **_: {"pending_approval_id": None})
    restored = router.chat_history(tid, current_user="dev")
    assert datetime.fromisoformat(restored["messages"][0]["created_at"]) == datetime.fromisoformat(row["created_at"])


def test_resume_result_persisted_once(monkeypatch):
    from app.api.v1 import router
    from app.schemas import ApprovalDecision

    tid = f"resume-history-{uuid4().hex}"
    approval = mail.prepare_email(
        user_id="dev", session_id=tid, to_email="teacher@example.com",
        subject="拟信", body="待确认正文",
    )
    monkeypatch.setattr(router.agent, "resume", lambda **_: "已恢复")
    decision = ApprovalDecision(
        approval_id=approval.id, decision="rejected",
        payload_version=approval.payload_version, session_id=tid,
    )
    router.chat_resume(decision, current_user="dev")
    router.chat_resume(decision, current_user="dev")
    messages = chat_store.list_messages(tid)
    assert len(messages) == 1
    assert messages[0]["content"] == "已拒绝，不会外发。"
