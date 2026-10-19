"""会话与消息持久化：列表/消息可从后端恢复（检查点另管执行态）。"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlmodel import func, select

from app.core.dates import now
from app.models.db import session_scope
from app.models.tables import Approval, ChatMessage, ChatThread


def check_thread_access(thread_id: str, user_id: str, *, allow_missing: bool = True) -> None:
    """校验归属，不创建会话；可用于上传前和 checkpoint 回退前。"""
    with session_scope() as session:
        row = session.get(ChatThread, thread_id)
        if (row and row.user_id != user_id) or (not row and not allow_missing):
            raise HTTPException(status_code=404, detail="thread not found")


def ensure_thread(thread_id: str, user_id: str = "dev", title: str = "") -> dict[str, Any]:
    with session_scope() as s:
        row = s.get(ChatThread, thread_id)
        if not row:
            row = ChatThread(
                id=thread_id, user_id=user_id, title=title or thread_id[:12]
            )
            s.add(row)
        else:
            if row.user_id != user_id:  # ← 新增
                raise HTTPException(
                    status_code=404,
                    detail="thread not found",  # 404 而非 403，不暴露会话存在
                )
            row.updated_at = now()
            if title and row.title in ("", "新会话", thread_id[:12]):
                row.title = title[:80]
        s.flush()
        return _thread_out(row)


def rename_thread(thread_id: str, title: str, user_id: str = "dev") -> dict[str, Any] | None:
    with session_scope() as s:
        row = s.get(ChatThread, thread_id)
        if not row or row.user_id != user_id:
            return None
        row.title = title[:80]
        row.updated_at = now()
        return _thread_out(row)


def delete_thread(thread_id: str, user_id: str = "dev") -> bool:
    with session_scope() as s:
        row = s.get(ChatThread, thread_id)
        if not row or row.user_id != user_id:
            return False
        s.delete(row)
        msgs = s.exec(
            select(ChatMessage).where(ChatMessage.thread_id == thread_id)
        ).all()
        for m in msgs:
            s.delete(m)
        return True


def list_threads(user_id: str = "dev") -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.exec(
            select(ChatThread)
            .where(ChatThread.user_id == user_id)
            .order_by(ChatThread.updated_at.desc())
        ).all()
        return [_thread_out(r) for r in rows]


def _thread_out(row: ChatThread) -> dict[str, Any]:
    return {
        "thread_id": row.id,
        "title": row.title,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def append_message(
    thread_id: str,
    role: str,
    content: str,
    *,
    user_id: str = "dev",
    card: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_thread(thread_id, user_id=user_id)
    with session_scope() as s:
        max_seq = s.exec(
            select(func.coalesce(func.max(ChatMessage.seq), 0)).where(
                ChatMessage.thread_id == thread_id
            )
        ).one()
        msg = ChatMessage(
            thread_id=thread_id,
            user_id=user_id,
            role=role,
            content=content,
            card=card,
            seq=int(max_seq or 0) + 1,
        )
        s.add(msg)
        thread = s.get(ChatThread, thread_id)
        if thread:
            thread.updated_at = now()
            if thread.title in ("", thread_id[:12]) and role == "user" and content:
                thread.title = content[:24]
        s.flush()
        return {
            "id": str(msg.id),
            "role": msg.role,
            "content": msg.content,
            "card": msg.card,
            "seq": msg.seq,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
        }


def list_messages(thread_id: str, user_id: str = "dev") -> list[dict[str, Any]]:
    check_thread_access(thread_id, user_id)
    with session_scope() as s:
        rows = s.exec(
            select(ChatMessage)
            .where(ChatMessage.thread_id == thread_id)
            .where(ChatMessage.user_id == user_id)
            .order_by(ChatMessage.seq)
        ).all()
        result = []
        for r in rows:
            card = dict(r.card) if r.card else None
            if card and card.get("type") == "approval":
                try:
                    approval = s.get(Approval, UUID(str(card.get("id"))))
                except (ValueError, TypeError):
                    approval = None
                if approval and approval.user_id == user_id:
                    card.update(
                        status=str(approval.status),
                        content_hash=approval.payload_version,
                        title=approval.payload.get("subject") or "邮件草稿",
                        body=f"{approval.payload.get('to_email', '')}\n{approval.payload.get('body', '')}",
                    )
                else:
                    card.update(status="unavailable", body="邮件记录不可用")
            result.append({
                "id": str(r.id),
                "role": r.role,
                "content": r.content,
                "card": card,
                "seq": r.seq,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })
        return result
