"""真实分发重入和持久化建单幂等；不连接真实 SMTP。"""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import create_engine, select

from app.agent import graph
from app.models.db import migrate_mail_intents, session_scope
from app.models.tables import Approval, Todo
from app.services import mail


def test_concurrent_prepare_and_sent_reprepare_share_one_intent():
    user, sid = uuid4().hex, uuid4().hex
    arguments = {"user_id": user, "session_id": sid, "to_email": "teacher@example.com", "subject": "same", "body": "same"}
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: mail.prepare_email(**arguments).id, range(4)))
    assert len(set(ids)) == 1
    first = mail.prepare_email(**arguments)
    result = mail.decide_approval(
        user_id=user,
        session_id=sid,
        approval_id=first.id,
        payload_version_in=first.payload_version,
        decision="approved",
    )
    assert result["executed"]
    for _ in range(3):
        assert mail.prepare_email(**arguments).id == first.id
        assert mail.prepare_email(**arguments).status == "sent"
        assert (
            mail.decide_approval(
                user_id=user,
                session_id=sid,
                approval_id=first.id,
                payload_version_in=first.payload_version,
                decision="approved",
            )["executed"]
            is False
        )


def test_force_new_interrupt_resume_never_replays_prior_writes_or_calls_model(
    monkeypatch,
):
    user, sid = uuid4().hex, uuid4().hex

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    model = Model(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "create_todo_tool",
                        "args": {"title": "only-once"},
                        "id": "todo",
                    },
                    {
                        "name": "prepare_email_tool",
                        "args": {
                            "to_email": "teacher@example.com",
                            "subject": "same",
                            "body": "approved snapshot",
                            "force_new": True,
                        },
                        "id": "mail",
                    },
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "prepare_email_tool",
                        "args": {
                            "to_email": "teacher@example.com",
                            "subject": "again",
                            "body": "must not run",
                            "force_new": True,
                        },
                        "id": "bad-extra",
                    }
                ],
            ),
        ]
    )
    monkeypatch.setattr(graph, "get_chat_model", lambda: model)
    agent = graph.LabPilotAgent.__new__(graph.LabPilotAgent)
    agent._saver = InMemorySaver()
    agent._graph = agent._build()
    reply = agent.chat(user_id=user, session_id=sid, user_text="新建任务并拟信")
    aid = reply.pending_approval_id
    assert aid
    approval = mail.get_approval(aid, user_id=user)
    assert model.i == 1
    result = mail.decide_approval(
        user_id=user,
        session_id=sid,
        approval_id=aid,
        payload_version_in=approval["payload_version"],
        decision="approved",
    )
    assert result["executed"]
    assert (
        agent.resume(user_id=user, session_id=sid, approved=True, approval_id=str(aid))
        == "图已恢复。"
    )
    assert model.i == 1, "approval resume must not ask the model to plan another send"
    with session_scope() as session:
        assert (
            len(session.exec(select(Approval).where(Approval.user_id == user)).all())
            == 1
        )
        assert len(session.exec(select(Todo).where(Todo.user_id == user)).all()) == 1
    assert not agent._graph.get_state(agent._config(sid, user)).next


def test_legacy_duplicates_preserve_sent_history_and_expire_pending(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE approvals (id TEXT PRIMARY KEY,user_id TEXT,session_id TEXT,payload_version TEXT,status TEXT,created_at TEXT,exec_result TEXT)"
                )
            )
            for aid, status in [("one", "sent"), ("two", "sent"), ("three", "pending")]:
                conn.execute(
                    text(
                        "INSERT INTO approvals VALUES (:id,'alice','thread','version',:status,'2026-10-15',NULL)"
                    ),
                    {"id": aid, "status": status},
                )
        migrate_mail_intents(engine)
        migrate_mail_intents(engine)
        with engine.connect() as conn:
            states = dict(conn.execute(text("SELECT id,status FROM approvals")).all())
            assert states == {"one": "sent", "two": "sent", "three": "expired"}
            key = conn.execute(
                text("SELECT intent_key FROM approvals WHERE id='one'")
            ).scalar_one()
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(
                text("INSERT INTO approvals (id,intent_key) VALUES ('duplicate',:key)"),
                {"key": key},
            )
    finally:
        engine.dispose()
