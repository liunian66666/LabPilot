"""纪要/文本 → 任务候选（规则 + 可选 LLM）。

优先确定性规则：相对日期、来源句；LLM 仅在可用时增强标题/交付物拆分。
"""

import re
from uuid import UUID

from app.core.dates import parse_relative_due
from app.core.logging import log_event
from app.models.db import session_scope
from app.models.tables import Document
from app.schemas import ExtractedTask, ExtractTasksResponse

# 常见任务触发词
_TASK_SPLIT = re.compile(r"[。；;\n]")
_CUE = re.compile(
    r"(任务|需要|要|待|负责|跟进|完成|补完|补齐|整理|提交|发送|汇报|修改|跑通|写完|对齐|实验|消融|跑一下)"
)


def _guess_priority(text: str):
    from app.models.tables import Priority

    if any(w in text for w in ("急", "尽快", "必须", "立刻", "ddl", "DDL", "截止")):
        return Priority.URGENT_IMPORTANT
    if any(w in text for w in ("重要", "关键", "实验", "消融", "投稿")):
        return Priority.IMPORTANT
    return Priority.NORMAL


def _guess_source(text: str):
    from app.models.tables import TodoSource

    if any(w in text for w in ("老师", "导师", "导", "supervisor")):
        return TodoSource.ADVISOR
    if any(w in text for w in ("组会", "会议", "例会")):
        return TodoSource.MEETING
    if any(w in text for w in ("论文", "paper", "投稿", "ddl", "DDL")):
        return TodoSource.PAPER_DDL
    return TodoSource.MEETING  # 纪要默认


def extract_tasks_rule(
    text: str, document_id: UUID | None = None
) -> ExtractTasksResponse:
    tasks: list[ExtractedTask] = []
    segments = [seg.strip() for seg in _TASK_SPLIT.split(text) if seg and seg.strip()]
    for seg in segments:
        if len(seg) < 6:
            continue
        if not _CUE.search(seg):
            continue
        due_raw = ""
        for token in (
            "下下周",
            "下周",
            "本周",
            "今天",
            "明天",
            "后天",
            "周日",
            "周天",
            "周六",
            "周五",
            "周四",
            "周三",
            "周二",
            "周一",
        ):
            if token in seg:
                # 取附近相对日期短语
                m = re.search(
                    r"([下本]?周?[一二三四五六日天]{1,2}前?|今天|明天|后天|\d{1,2}月\d{1,2}日)",
                    seg,
                )
                if m:
                    due_raw = m.group(1)
                break
        if not due_raw:
            m = re.search(r"(\d{4}-\d{2}-\d{2}|\d{1,2}月\d{1,2}日)", seg)
            if m:
                due_raw = m.group(1)

        due_date, needs = parse_relative_due(due_raw) if due_raw else (None, True)
        if not due_raw:
            needs = True

        # 「周五前/截止」→ due_at；「周五做」→ planned_date
        is_deadline = bool(due_raw) and any(
            w in seg for w in ("前", "截止", "ddl", "DDL", "之前", "deadline")
        )
        due_at = None
        planned = due_date
        if due_date and is_deadline:
            from app.core.dates import end_of_day

            due_at = end_of_day(due_date)
            planned = None

        deliverable = ""
        if "给老师" in seg or "发给" in seg or "汇报" in seg:
            deliverable = "对外同步/汇报结果"

        tasks.append(
            ExtractedTask(
                title=seg[:80],
                detail=seg,
                due_raw=due_raw,
                due_at=due_at,
                planned_date=planned,
                needs_confirm=needs and due_at is None and planned is None,
                priority=_guess_priority(seg),
                source=_guess_source(seg),
                source_quote=seg,
                deliverable=deliverable,
            )
        )
        if len(tasks) >= 12:
            break

    log_event(
        "extract_tasks_done",
        task_count=len(tasks),
        document_id=str(document_id) if document_id else "",
    )
    return ExtractTasksResponse(tasks=tasks, document_id=document_id)


def store_document(
    title: str,
    content: str,
    doc_type: str = "meeting_note",
    user_id: str = "dev",
    filename: str = "",
) -> UUID:
    import hashlib

    from app.models.tables import Chunk
    from app.services.rag import split_text

    h = hashlib.sha256(content.encode("utf-8")).hexdigest()
    chunks = split_text(content) or ([content.strip()] if content.strip() else [])
    with session_scope() as s:
        doc = Document(
            user_id=user_id,
            title=title,
            filename=filename or title,
            content_hash=h,
            doc_type=doc_type,
        )
        s.add(doc)
        s.flush()
        for i, text in enumerate(chunks):
            s.add(
                Chunk(
                    document_id=doc.id,
                    content=text,
                    seq=i,
                    page_or_section=f"chunk-{i}",
                    embedding_json=None,  # 入库向量化由 RAG ingest 异步/显式触发
                )
            )
        return doc.id  # type: ignore[return-value]


def persist_by_document(document_id: UUID, user_id: str = "dev") -> ExtractTasksResponse | None:
    """确认后的第二步：复用已入库的纪要文档原文，重新抽取并写入 todos。

    规则抽取是确定性的，同一段原文候选不会漂移；复用 document_id 保证证据链一致。
    文档不存在或不属于该用户时返回 None。
    """
    from sqlmodel import select

    from app.models.tables import Chunk

    with session_scope() as s:
        doc = s.get(Document, document_id)
        if doc is None or doc.user_id != user_id:
            return None
        chunks = s.exec(
            select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.seq)
        ).all()
        text = "\n".join(c.content for c in chunks)
    resp = extract_tasks_rule(text, document_id=document_id)
    return persist_extracted_tasks(resp, user_id=user_id, document_id=document_id)


def extract_and_persist_note(
    text: str,
    title: str = "meeting_note",
    user_id: str = "dev",
    persist: bool = False,
) -> ExtractTasksResponse:
    doc_id = store_document(
        title=title, content=text, doc_type="meeting_note", user_id=user_id
    )
    resp = extract_tasks_rule(text, document_id=doc_id)
    if persist:
        resp = persist_extracted_tasks(resp, user_id=user_id, document_id=doc_id)
    return resp


def persist_extracted_tasks(
    resp: ExtractTasksResponse,
    *,
    user_id: str = "dev",
    document_id: UUID | None = None,
) -> ExtractTasksResponse:
    """把抽取候选写入 todos，保留证据链。"""
    from app.schemas import TodoCreate
    from app.services import todo as todo_svc

    doc_id = document_id or resp.document_id
    updated: list[ExtractedTask] = []
    for task in resp.tasks:
        out = todo_svc.create_todo(
            TodoCreate(
                title=task.title,
                detail=task.detail or task.source_quote,
                due_at=task.due_at,
                planned_date=task.planned_date,
                priority=task.priority,
                source=task.source,
                source_quote=task.source_quote,
                origin_document_id=doc_id,
                confirm_state="needs_confirm" if task.needs_confirm else "confirmed",
            ),
            user_id=user_id,
            actor="agent",
        )
        updated.append(task.model_copy(update={"todo_id": out.id}))
    return ExtractTasksResponse(tasks=updated, document_id=doc_id)
