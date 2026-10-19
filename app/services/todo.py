"""四维待办服务：状态事实 + todo_events；今日/本周/逾期动态计算。"""

from typing import Any
from uuid import UUID

from sqlmodel import select

from app.core.dates import now, today, week_range
from app.core.logging import log_event
from app.models.db import session_scope
from app.models.tables import Priority, Todo, TodoEvent, TodoSource, TodoStatus
from app.schemas import TodoCreate, TodoOut, TodoUpdate


def _to_out(t: Todo) -> TodoOut:
    def _enum(val, cls):
        try:
            return cls(val)
        except ValueError:
            return val

    return TodoOut(
        id=t.id,
        title=t.title,
        detail=t.detail,
        due_at=t.due_at,
        planned_date=t.planned_date,
        priority=_enum(t.priority, Priority),
        source=_enum(t.source, TodoSource),
        status=_enum(t.status, TodoStatus),
        confirm_state=t.confirm_state,
        source_quote=t.source_quote,
        remind_at=getattr(t, "remind_at", None),
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


def create_todo(data: TodoCreate, user_id: str = "dev", actor: str = "user") -> TodoOut:
    with session_scope() as s:
        payload = data.model_dump()
        todo = Todo(**payload, user_id=user_id)
        s.add(todo)
        s.flush()
        st = str(todo.status)
        s.add(
            TodoEvent(
                todo_id=todo.id,
                event_type="created",
                to_status=st,
                note=data.source_quote[:200] if data.source_quote else "",
                actor=actor,
            )
        )
        log_event(
            "todo_created",
            todo_id=str(todo.id),
            user_id=user_id,
            actor=actor,
            priority=str(todo.priority),
        )
        return _to_out(todo)


def list_todos(user_id: str = "dev", status: TodoStatus | None = None) -> list[TodoOut]:
    with session_scope() as s:
        stmt = select(Todo).where(Todo.user_id == user_id)
        if status:
            stmt = stmt.where(Todo.status == status)
        rows = s.exec(stmt.order_by(Todo.created_at.desc())).all()
        return [_to_out(t) for t in rows]


def get_todo(todo_id: UUID, user_id: str = "dev") -> TodoOut | None:
    with session_scope() as s:
        t = s.get(Todo, todo_id)
        if not t or t.user_id != user_id:
            return None
        return _to_out(t)


def update_todo(
    todo_id: UUID, data: TodoUpdate, user_id: str = "dev"
) -> TodoOut | None:
    with session_scope() as s:
        t = s.get(Todo, todo_id)
        if not t or t.user_id != user_id:
            return None
        old_status = str(t.status)
        payload = data.model_dump(exclude_unset=True)
        note = payload.pop("note", "")
        for k, v in payload.items():
            setattr(t, k, v)
        t.updated_at = now()
        st = str(t.status)
        if st == TodoStatus.DONE and t.completed_at is None:
            t.completed_at = now()
        if st != TodoStatus.DONE:
            t.completed_at = None
        # 补齐日期后自动完成确认
        if (
            getattr(t, "confirm_state", None) == "needs_confirm"
            and (t.due_at or t.planned_date)
            and "confirm_state" not in payload
        ):
            t.confirm_state = "confirmed"
        event_type = "updated"
        if st == TodoStatus.DONE:
            event_type = "completed"
        elif st == TodoStatus.CANCELLED:
            event_type = "cancelled"
        elif st == TodoStatus.BLOCKED:
            event_type = "blocked"
        elif "due_at" in payload:
            event_type = "delayed"
        s.add(
            TodoEvent(
                todo_id=t.id,
                event_type=event_type,
                from_status=old_status,
                to_status=st,
                note=note,
                actor="user",
            )
        )
        log_event(
            "todo_updated",
            todo_id=str(t.id),
            user_id=user_id,
            event_type=event_type,
            status=st,
        )
        return _to_out(t)


def todo_views(user_id: str = "dev") -> dict[str, list[dict[str, Any]]]:
    """动态计算 today / week / overdue / waiting（不落库 time_bucket）。"""
    d_today = today()
    mon, sun = week_range(d_today)
    now_ts = now()
    with session_scope() as s:
        rows = s.exec(select(Todo).where(Todo.user_id == user_id)).all()
        open_rows = [
            t for t in rows if t.status not in (TodoStatus.DONE, TodoStatus.CANCELLED)
        ]

        def as_dict(t: Todo) -> dict[str, Any]:
            return {
                "id": str(t.id),
                "title": t.title,
                "due_at": t.due_at.isoformat() if t.due_at else None,
                "planned_date": t.planned_date.isoformat() if t.planned_date else None,
                "priority": str(t.priority),
                "source": str(t.source),
                "status": str(t.status),
            }

        today_list = []
        week_list = []
        overdue = []
        waiting = []
        for t in open_rows:
            if str(t.status) == TodoStatus.WAITING:
                waiting.append(as_dict(t))
            due_date = t.due_at.date() if t.due_at else None
            planned_overdue = (
                t.planned_date is not None and t.planned_date < d_today and not t.due_at
            )
            is_overdue = bool((t.due_at and t.due_at < now_ts) or planned_overdue)
            if is_overdue:
                overdue.append(as_dict(t))
            if (t.planned_date == d_today) or (due_date == d_today) or is_overdue:
                today_list.append(as_dict(t))
            if due_date and mon <= due_date <= sun:
                week_list.append(as_dict(t))
            if (
                t.planned_date
                and mon <= t.planned_date <= sun
                and as_dict(t) not in week_list
            ):
                week_list.append(as_dict(t))

        prio_rank = {
            "urgent_important": 0,
            "important": 1,
            "urgent": 2,
            "normal": 3,
        }
        for lst in (today_list, week_list, overdue, waiting):
            lst.sort(
                key=lambda x: (prio_rank.get(x["priority"], 9), x.get("due_at") or "")
            )

        return {
            "today": today_list,
            "this_week": week_list,
            "overdue": overdue,
            "waiting": waiting,
        }
