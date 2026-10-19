"""Pydantic 请求/响应模型。"""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.tables import Priority, TodoSource, TodoStatus


class AuthRegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=6, max_length=128)


class AuthLoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    username: str = ""


class SessionCreate(BaseModel):
    title: str = Field(default="default")


class SessionOut(BaseModel):
    session_id: str
    title: str


class ChatMessage(BaseModel):
    role: str = Field(default="user")
    content: str = Field(min_length=1, max_length=8000)


class PreferenceBody(BaseModel):
    key: str
    value: str


class ReminderSettingBody(BaseModel):
    enabled: bool = False
    to_email: str = Field(default="", max_length=254)
    hours_before: int = Field(default=24, ge=1, le=336)


class ChatRequest(BaseModel):
    session_id: str = Field(default="dev")
    messages: list[ChatMessage] = Field(min_length=1)
    # 可选：当前操作对象，写入短期上下文，减少「这份/刚才那个」猜测
    active_report_id: str | None = None
    active_todo_id: str | None = None
    active_approval_id: str | None = None


class Artifact(BaseModel):
    type: str  # report | todo | approval | progress
    id: str
    title: str = ""
    status: str = ""
    content_hash: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    pending_approval_id: UUID | None = None
    tools_used: list[str] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)


class ExtractTasksRequest(BaseModel):
    text: str = Field(min_length=1)
    document_id: UUID | None = None
    source_label: str = Field(default="inline_note")
    # true=将任务候选写入 todos（含 source_quote / origin_document_id）
    persist: bool = False


class ExtractedTask(BaseModel):
    title: str
    detail: str = ""
    due_raw: str = ""
    due_at: datetime | None = None
    planned_date: date | None = None
    needs_confirm: bool = True
    priority: Priority = Priority.NORMAL
    source: TodoSource = TodoSource.MEETING
    source_quote: str = ""
    deliverable: str = ""
    todo_id: UUID | None = None


class ExtractTasksResponse(BaseModel):
    tasks: list[ExtractedTask]
    document_id: UUID | None = None


class TodoCreate(BaseModel):
    title: str = Field(min_length=1)
    detail: str = ""
    due_at: datetime | None = None
    planned_date: date | None = None
    # 自然语言日期（如「下周五前」）；due_at/planned_date 均未提供时由服务端解析
    due_raw: str = ""
    remind_at: datetime | None = None  # 自定义定时提醒时刻
    priority: Priority = Priority.NORMAL
    source: TodoSource = TodoSource.PERSONAL
    source_quote: str = ""
    origin_document_id: UUID | None = None
    origin_chunk_id: UUID | None = None
    confirm_state: str = "confirmed"


class TodoUpdate(BaseModel):
    title: str | None = None
    detail: str | None = None
    due_at: datetime | None = None
    planned_date: date | None = None
    # 自然语言日期；提供时覆盖 due_at/planned_date（自动判定截止或计划）
    due_raw: str = ""
    remind_at: datetime | None = None  # 显式传入 None = 清除提醒
    priority: Priority | None = None
    source: TodoSource | None = None
    status: TodoStatus | None = None
    confirm_state: str | None = None  # confirmed | needs_confirm
    note: str = ""


class TodoOut(BaseModel):
    id: UUID
    title: str
    detail: str
    due_at: datetime | None
    planned_date: date | None
    remind_at: datetime | None = None
    priority: Priority
    source: TodoSource
    status: TodoStatus
    confirm_state: str
    source_quote: str | None
    created_at: datetime
    updated_at: datetime


class ProgressCreate(BaseModel):
    work_done: str = Field(min_length=1)
    work_date: date | None = None
    todo_id: UUID | None = None
    observations: str = ""
    blockers: str = ""


class ReportRequest(BaseModel):
    period_start: date | None = None
    period_end: date | None = None


class ReportOut(BaseModel):
    id: UUID
    period_start: date
    period_end: date
    body_markdown: str
    source_refs: dict[str, Any]
    status: str


class EmailPrepare(BaseModel):
    to_email: str
    subject: str
    body: str
    to_name: str = ""
    session_id: str = "dev"


class ApprovalDecision(BaseModel):
    approval_id: UUID
    decision: str = Field(pattern="^(approved|rejected)$")
    payload_version: str
    session_id: str = "dev"


class ApprovalOut(BaseModel):
    id: UUID
    status: str
    payload: dict[str, Any]
    payload_version: str
    exec_result: str | None
