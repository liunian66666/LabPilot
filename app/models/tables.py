"""SQLModel 数据模型。"""

from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Column, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


def _now() -> datetime:
    from app.core.dates import now

    return now()


class Priority(StrEnum):
    URGENT_IMPORTANT = "urgent_important"
    IMPORTANT = "important"
    URGENT = "urgent"
    NORMAL = "normal"


class TodoSource(StrEnum):
    MEETING = "meeting"
    ADVISOR = "advisor"
    PAPER_DDL = "paper_ddl"
    PERSONAL = "personal"
    COMM = "comm"


class TodoStatus(StrEnum):
    TODO = "todo"
    DOING = "doing"
    WAITING = "waiting"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


class ApprovalStatus(StrEnum):
    PROPOSED = "proposed"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    UNKNOWN = "unknown"


class User(SQLModel, table=True):
    """登录账号：密码以 bcrypt 加盐哈希存储，不存明文。"""

    __tablename__ = "users"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    username: str = Field(unique=True, index=True, min_length=3, max_length=32)
    password_hash: str
    created_at: datetime = Field(default_factory=_now)


class Integration(SQLModel, table=True):
    """按 LabPilot 用户保存外部服务凭证；仅持久化密文。"""

    __tablename__ = "integrations"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_integration_user_provider"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: str = Field(index=True)
    provider: str = Field(default="zotero")
    zotero_user_id: str
    api_key_enc: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Document(SQLModel, table=True):
    __tablename__ = "documents"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: str = Field(index=True, default="dev")
    title: str = Field(default="")
    filename: str = Field(default="")
    content_hash: str = Field(default="")# 正文的 sha256 指纹（不是正文！）
    version: int = Field(default=1)
    doc_type: str = Field(
        default="other"
    )  # meeting_note | paper | experiment_log | other
    created_at: datetime = Field(default_factory=_now)


class Chunk(SQLModel, table=True):
    __tablename__ = "chunks"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    document_id: UUID = Field(foreign_key="documents.id", index=True)
    content: str = Field(sa_column=Column(Text, nullable=False))
    seq: int = Field(default=0)
    page_or_section: str = Field(default="")
    embedding_json: list[float] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now)


class Todo(SQLModel, table=True):
    __tablename__ = "todos"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: str = Field(index=True, default="dev")
    title: str
    detail: str = Field(default="", sa_column=Column(Text))
    due_at: datetime | None = Field(default=None)
    planned_date: date | None = Field(default=None)
    priority: str = Field(default="normal")
    source: str = Field(default="personal")
    status: str = Field(default="todo")
    confirm_state: str = Field(default="confirmed")  # confirmed | needs_confirm
    origin_session_id: str | None = Field(default=None)
    origin_document_id: UUID | None = Field(default=None)
    origin_chunk_id: UUID | None = Field(default=None)
    source_quote: str | None = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    completed_at: datetime | None = Field(default=None)
    # 自定义定时提醒时刻（精确到分钟，到点即发，与截止提前量无关）
    remind_at: datetime | None = Field(default=None, index=True)


class TodoEvent(SQLModel, table=True):
    __tablename__ = "todo_events"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    todo_id: UUID = Field(foreign_key="todos.id", index=True)
    event_type: (
        str  # created | updated | delayed | blocked | completed | cancelled | note
    )
    from_status: str | None = Field(default=None)
    to_status: str | None = Field(default=None)
    note: str = Field(default="", sa_column=Column(Text))
    actor: str = Field(default="user")  # user | agent
    created_at: datetime = Field(default_factory=_now)


class ProgressEntry(SQLModel, table=True):
    __tablename__ = "progress_entries"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: str = Field(index=True, default="dev")
    work_date: date = Field(default_factory=lambda: _now().date())
    todo_id: UUID | None = Field(default=None)
    work_done: str = Field(sa_column=Column(Text))
    observations: str = Field(default="", sa_column=Column(Text))
    blockers: str = Field(default="", sa_column=Column(Text))
    source_document_id: UUID | None = Field(default=None)
    source_quote: str | None = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class ReportDraft(SQLModel, table=True):
    __tablename__ = "report_drafts"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: str = Field(index=True, default="dev")
    period_start: date
    period_end: date
    body_markdown: str = Field(sa_column=Column(Text))
    source_refs: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = Field(default="draft")  # draft | edited | sent
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Approval(SQLModel, table=True):
    __tablename__ = "approvals"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: str = Field(index=True, default="dev")
    session_id: str = Field(index=True, default="dev")
    action_type: str = Field(default="send_email")
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    payload_version: str = Field(index=True)
    intent_key: str | None = Field(default=None, unique=True, index=True)
    status: str = Field(default="pending")  # ApprovalStatus value
    decided_at: datetime | None = Field(default=None)
    decided_by: str | None = Field(default=None)
    exec_attempted_at: datetime | None = Field(default=None)
    exec_result: str | None = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class AuditEvent(SQLModel, table=True):
    __tablename__ = "audit_events"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: str = Field(index=True, default="dev")
    session_id: str = Field(default="")
    event_type: str = Field(index=True)
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now)


class ChatThread(SQLModel, table=True):
    """后端会话索引：浏览器清缓存后仍可恢复列表。"""

    __tablename__ = "chat_threads"

    id: str = Field(primary_key=True)  # thread_id
    user_id: str = Field(index=True, default="dev")
    title: str = Field(default="")
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class ChatMessage(SQLModel, table=True):
    """用户可见聊天记录（与检查点分离：负责 UI 还原）。"""

    __tablename__ = "chat_messages"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    thread_id: str = Field(index=True)
    user_id: str = Field(index=True, default="dev")
    role: str = Field(default="user")  # user | assistant | card
    content: str = Field(sa_column=Column(Text))
    card: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    seq: int = Field(default=0)
    created_at: datetime = Field(default_factory=_now)


class UserPreference(SQLModel, table=True):
    """长期记忆：用户明确「以后都…」才写入；临时要求不落库。"""

    __tablename__ = "user_preferences"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: str = Field(index=True, default="dev")
    key: str = Field(index=True)  # research_focus | report_sections | advisor_salutation | report_tone
    value: str = Field(sa_column=Column(Text))
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class ReminderSetting(SQLModel, table=True):
    """自助截止提醒：开启时一次性授权，到点自动发送（非 HITL）。"""

    __tablename__ = "reminder_settings"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: str = Field(unique=True, index=True, default="dev")
    enabled: bool = Field(default=False)
    to_email: str = Field(default="")
    hours_before: int = Field(default=24)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class ReminderLog(SQLModel, table=True):
    """已发送提醒记录，防重复；截止变更后重新计算。"""

    __tablename__ = "reminder_logs"
    __table_args__ = (UniqueConstraint("user_id", "todo_id", "due_key", name="uq_reminder_due"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: str = Field(index=True, default="dev")
    todo_id: UUID = Field(index=True)
    due_key: str = Field(index=True)  # due_at/planned 规范化字符串，改期后 key 变化
    sent_at: datetime = Field(default_factory=_now)
    status: str = Field(default="sent")  # sending | sent | failed | skipped | unknown
    detail: str = Field(default="", sa_column=Column(Text))
