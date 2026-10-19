"""周报草稿：只聚合区间内已有进展与任务事件，无记录标「待补充」。"""

from datetime import date
from typing import Any

from sqlmodel import select

from app.core.dates import now, today, week_range
from app.core.logging import log_event
from app.models.db import session_scope
from app.models.tables import ProgressEntry, ReportDraft, Todo, TodoEvent, TodoStatus


def generate_weekly_report(
    user_id: str = "dev",
    period_start: date | None = None,
    period_end: date | None = None,
) -> ReportDraft:
    if period_start is None or period_end is None:
        mon, sun = week_range(today())
        period_start = period_start or mon
        period_end = period_end or sun

    with session_scope() as s:
        progress = s.exec(
            select(ProgressEntry)
            .where(ProgressEntry.user_id == user_id)
            .where(ProgressEntry.work_date >= period_start)
            .where(ProgressEntry.work_date <= period_end)
            .order_by(ProgressEntry.work_date)
        ).all()
        todos = s.exec(select(Todo).where(Todo.user_id == user_id)).all()
        all_events = s.exec(
            select(TodoEvent)
            .join(Todo, TodoEvent.todo_id == Todo.id)
            .where(Todo.user_id == user_id)
        ).all()
        events = [
            e
            for e in all_events
            if e.created_at and period_start <= e.created_at.date() <= period_end
        ]

        lines: list[str] = []
        ref_progress = [str(p.id) for p in progress]
        ref_events = [str(e.id) for e in events]

        # 长期偏好：周报小节（缺省三段）
        from app.services.preferences import get_preferences

        prefs = get_preferences(user_id)
        section_titles = [
            x.strip()
            for x in (prefs.get("report_sections") or "本周进展,待办完成情况,下周计划,需协调事项").split(
                ","
            )
            if x.strip()
        ]
        salutation = prefs.get("advisor_salutation", "")
        if salutation:
            lines.append(f"> 称呼偏好：{salutation}")
            lines.append("")

        lines.append(
            f"# 周报草稿（{period_start.isoformat()} ~ {period_end.isoformat()}）"
        )
        lines.append("")

        def section_or_default(idx: int, fallback: str) -> str:
            return section_titles[idx] if idx < len(section_titles) else fallback

        lines.append(f"## {section_or_default(0, '本周进展')}")
        if progress:
            for p in progress:
                bullet = f"- [{p.work_date.isoformat()}] {p.work_done}"
                if p.observations:
                    bullet += f"；观察：{p.observations}"
                if p.blockers:
                    bullet += f"；阻塞：{p.blockers}"
                lines.append(bullet)
        else:
            lines.append("- 【待补充】本周尚无进展记录，不自动编造实验结果或结论。")

        lines.append("")
        lines.append(f"## {section_or_default(1, '待办完成情况')}")
        done_in_week = [
            t
            for t in todos
            if str(t.status) == TodoStatus.DONE
            and t.completed_at
            and period_start <= t.completed_at.date() <= period_end
        ]
        if done_in_week:
            for t in done_in_week:
                lines.append(f"- ✅ {t.title}")
        else:
            lines.append("- 【待补充】本区间内无标记完成的任务。")

        lines.append("")
        lines.append(f"## {section_or_default(2, '未完成 / 下周计划')}")
        open_todos = [
            t
            for t in todos
            if t.status
            in (
                TodoStatus.TODO,
                TodoStatus.DOING,
                TodoStatus.WAITING,
                TodoStatus.BLOCKED,
            )
        ]
        if open_todos:
            for t in open_todos[:15]:
                due = (
                    t.due_at.isoformat()
                    if t.due_at
                    else (str(t.planned_date) if t.planned_date else "无截止")
                )
                lines.append(
                    f"- [{t.status!s}/{t.priority!s}] {t.title}（截止：{due}）"
                )
        else:
            lines.append("- 【待补充】")

        lines.append("")
        lines.append(f"## {section_or_default(3, '需协调事项')}")
        blockers = [p for p in progress if p.blockers]
        waiting = [t for t in todos if str(t.status) == TodoStatus.WAITING]
        if blockers:
            for p in blockers:
                lines.append(f"- 阻塞：{p.blockers}")
        if waiting:
            for t in waiting:
                lines.append(f"- 等待中：{t.title}")
        if not blockers and not waiting:
            lines.append("- 【待补充】")

        body = "\n".join(lines)
        source_refs: dict[str, Any] = {
            "progress_entry_ids": ref_progress,
            "todo_event_ids": ref_events,
            "note": "仅聚合已有记录；缺失内容显示为待补充",
        }

        draft = ReportDraft(
            user_id=user_id,
            period_start=period_start,
            period_end=period_end,
            body_markdown=body,
            source_refs=source_refs,
            status="draft",
        )
        s.add(draft)
        s.flush()
        log_event(
            "report_generated",
            report_id=str(draft.id),
            user_id=user_id,
            period_start=period_start.isoformat(),
            period_end=period_end.isoformat(),
            progress_count=len(progress),
        )
        # 拷贝字段，避免 session 结束后 DetachedInstanceError
        return ReportDraftView(
            id=draft.id,
            user_id=user_id,
            period_start=period_start,
            period_end=period_end,
            body_markdown=body,
            source_refs=source_refs,
            status="draft",
        )


class ReportDraftView:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def get_report(report_id: str, user_id: str = "dev") -> dict[str, Any] | None:
    from uuid import UUID

    with session_scope() as s:
        try:
            rid = UUID(str(report_id))
        except ValueError:
            return None
        row = s.get(ReportDraft, rid)
        if not row or row.user_id != user_id:
            return None
        return {
            "id": str(row.id),
            "period_start": row.period_start.isoformat(),
            "period_end": row.period_end.isoformat(),
            "body_markdown": row.body_markdown,
            "source_refs": row.source_refs,
            "status": row.status,
            "content_hash": _body_hash(row.body_markdown),
        }


def _body_hash(body: str) -> str:
    import hashlib

    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()[:16]


def edit_report(
    report_id: str,
    body_markdown: str,
    *,
    user_id: str = "dev",
    expected_hash: str | None = None,
) -> dict[str, Any]:
    """编辑草稿；**必须**携带 expected_hash，冲突则拒绝覆盖。"""
    from uuid import UUID

    if not expected_hash:
        return {
            "ok": False,
            "error": "content_version_required",
            "hint": "先 get_report 取 content_hash，再带 expected_hash 编辑",
        }
    with session_scope() as s:
        try:
            rid = UUID(str(report_id))
        except ValueError:
            return {"ok": False, "error": "invalid_id"}
        row = s.get(ReportDraft, rid)
        if not row or row.user_id != user_id:
            return {"ok": False, "error": "not_found"}
        if _body_hash(row.body_markdown) != expected_hash:
            return {
                "ok": False,
                "error": "content_version_mismatch",
                "current_hash": _body_hash(row.body_markdown),
            }
        row.body_markdown = body_markdown
        row.status = "edited"
        row.updated_at = now()
        s.flush()
        log_event(
            "report_edited",
            report_id=str(row.id),
            user_id=user_id,
            body_len=len(body_markdown),
        )
        return {
            "ok": True,
            "id": str(row.id),
            "status": row.status,
            "content_hash": _body_hash(row.body_markdown),
            "body_markdown": row.body_markdown,
        }
