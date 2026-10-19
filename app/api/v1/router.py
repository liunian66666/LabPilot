"""API 路由（FastAPI，分层与 FastGraph 类似：路由只做校验与调服务）。"""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.agent.graph import agent
from app.core.admission import acquire, guard_chat
from app.core.logging import log_event
from app.core.security import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.models.db import session_scope
from app.models.tables import User
from app.schemas import (
    ApprovalDecision,
    ApprovalOut,
    AuthLoginRequest,
    AuthRegisterRequest,
    ChatRequest,
    ChatResponse,
    ExtractTasksRequest,
    ExtractTasksResponse,
    PreferenceBody,
    ProgressCreate,
    ReminderSettingBody,
    ReportOut,
    ReportRequest,
    SessionCreate,
    SessionOut,
    TodoCreate,
    TodoOut,
    TodoUpdate,
    TokenResponse,
)
from app.services import extract as extract_svc
from app.services import mail as mail_svc
from app.services import report as report_svc
from app.services import todo as todo_svc
from app.services.audit import audit

router = APIRouter()


@router.post("/auth/register", response_model=TokenResponse, status_code=201)
def register(body: AuthRegisterRequest) -> TokenResponse:
    """注册账号（bcrypt 哈希密码），注册即登录并签发 JWT。"""
    from sqlmodel import select

    username = body.username.strip()
    with session_scope() as s:
        if s.exec(select(User).where(User.username == username)).first():
            raise HTTPException(status_code=409, detail="username already taken")
        user = User(username=username, password_hash=hash_password(body.password))
        s.add(user)
        s.flush()
        uid = str(user.id)
    audit(username, "user_registered", username=username)
    return TokenResponse(
        access_token=create_access_token(username),
        user_id=uid,
        username=username,
    )


@router.post("/auth/login", response_model=TokenResponse)
def login(body: AuthLoginRequest) -> TokenResponse:
    """校验账号密码，通过后签发 JWT。"""
    from sqlmodel import select

    username = body.username.strip()
    with session_scope() as s:
        user = s.exec(select(User).where(User.username == username)).first()
        if not user or not verify_password(body.password, user.password_hash):
            raise HTTPException(
                status_code=401, detail="incorrect username or password"
            )
        uid = str(user.id)
    audit(username, "user_login")
    return TokenResponse(
        access_token=create_access_token(username),
        user_id=uid,
        username=username,
    )


@router.post("/sessions", response_model=SessionOut)
def create_session(
    body: SessionCreate, current_user: str = Depends(get_current_user)
) -> SessionOut:
    return SessionOut(
        session_id=body.title or current_user, title=body.title or current_user
    )


@router.post("/chat/new-task")
def new_task(current_user: str = Depends(get_current_user)) -> dict:
    """开始新任务：客户端换新 thread_id；长期偏好与业务记录保留，待审批仍可从审批列表恢复。"""
    from uuid import uuid4

    audit(current_user, "chat_new_task")
    return {"thread_id": str(uuid4())}


@router.get("/preferences")
def get_prefs(current_user: str = Depends(get_current_user)) -> dict:
    from app.services.preferences import get_preferences

    return {"preferences": get_preferences(current_user)}


@router.put("/preferences")
def put_pref(
    body: PreferenceBody, current_user: str = Depends(get_current_user)
) -> dict:
    from app.services.preferences import set_preference

    r = set_preference(current_user, body.key, body.value)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=str(r))
    audit(current_user, "preference_saved", key=body.key)
    return r


@router.delete("/preferences/{key}")
def del_pref(
    key: str, current_user: str = Depends(get_current_user)
) -> dict:
    from app.services.preferences import delete_preference

    return delete_preference(current_user, key)


@router.post("/chat", response_model=ChatResponse)
@guard_chat
def chat(
    body: ChatRequest, current_user: str = Depends(get_current_user)
) -> ChatResponse:
    user_text = body.messages[-1].content
    # 短期上下文：thread_id=session_id（检查点）+ 显式对象 ID 提示
    hints = []
    if body.active_report_id:
        hints.append(f"[active_report_id={body.active_report_id}]")
    if body.active_todo_id:
        hints.append(f"[active_todo_id={body.active_todo_id}]")
    if body.active_approval_id:
        hints.append(f"[active_approval_id={body.active_approval_id}]")
    if hints:
        user_text = " ".join(hints) + " " + user_text
    from app.services import chat_store

    chat_store.ensure_thread(body.session_id, user_id=current_user, title=body.messages[-1].content[:24])
    chat_store.append_message(
        body.session_id, "user", user_text, user_id=current_user
    )
    log_event(
        "chat_api_start",
        session_id=body.session_id,
        user_id=current_user,
        content_len=len(user_text),
    )
    resp = agent.chat(user_id=current_user, session_id=body.session_id, user_text=user_text)
    log_event(
        "chat_api_done",
        session_id=body.session_id,
        reply_len=len(resp.reply or ""),
        artifact_count=len(resp.artifacts or []),
        pending_approval=bool(resp.pending_approval_id),
    )
    chat_store.append_message(
        body.session_id, "assistant", resp.reply, user_id=current_user
    )
    for art in resp.artifacts or []:
        chat_store.append_message(
            body.session_id,
            "card",
            art.title or art.type,
            user_id=current_user,
            card={
                "type": art.type,
                "id": art.id,
                "title": art.title,
                "status": art.status,
                "content_hash": art.content_hash,
            },
        )
    return resp


@router.post("/chat/stream")
def chat_stream(
    body: ChatRequest, current_user: str = Depends(get_current_user)
):
    from fastapi.responses import StreamingResponse
    from starlette.background import BackgroundTask

    from app.services.chat_store import check_thread_access
    from app.services.chat_stream import stream_chat_events
    check_thread_access(body.session_id, current_user)
    lease = acquire(current_user, body.session_id)

    return StreamingResponse(
        stream_chat_events(agent, body, current_user, lease=lease), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        background=BackgroundTask(lease.release_if_unstarted),
    )


@router.post("/chat/resume")
@guard_chat
def chat_resume(
    body: ApprovalDecision, current_user: str = Depends(get_current_user)
) -> dict:
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(
            status_code=400, detail="decision must be approved|rejected"
        )
    result = mail_svc.decide_approval(
        user_id=current_user,
        approval_id=body.approval_id,
        decision=body.decision,
        payload_version_in=body.payload_version,
        session_id=body.session_id,
    )
    if not result.get("ok"):
        error = result.get("error", "conflict")
        if error in {"approval_not_found", "forbidden"}:
            raise HTTPException(status_code=404, detail="approval not found")
        raise HTTPException(status_code=409, detail=error)
    # 恢复图；结果必须反馈给调用方（审批成功 ≠ 图恢复成功）
    resume_msg = "审批请求已处理，未重复恢复执行。" if result.get("idempotent") else agent.resume(
        user_id=current_user,
        session_id=body.session_id,
        approved=body.decision == "approved",
        approval_id=str(body.approval_id),
    )
    result = dict(result)
    result["graph_resume"] = resume_msg
    if not result.get("idempotent"):
        from app.services import chat_store

        labels = {
            "sent": "邮件已发送。", "rejected": "已拒绝，不会外发。",
            "failed": "邮件发送失败。", "unknown": "邮件发送结果待核查，请勿重复发送。",
            "sending": "邮件发送中。", "approved": "邮件已批准。",
        }
        chat_store.append_message(
            body.session_id, "assistant",
            labels.get(str(result.get("status")), "邮件处理结果请查看邮件卡片。"),
            user_id=current_user,
        )
    return result


@router.post("/notes/extract", response_model=ExtractTasksResponse)
def extract_note(
    body: ExtractTasksRequest, current_user: str = Depends(get_current_user)
) -> ExtractTasksResponse:
    resp = extract_svc.extract_and_persist_note(
        body.text, user_id=current_user, persist=body.persist
    )
    audit(
        current_user,
        "extract_tasks",
        count=len(resp.tasks),
        persist=body.persist,
        document_id=str(resp.document_id or ""),
    )
    return resp


def _resolve_due_raw(values: dict, *, only_if_unset: bool) -> dict:
    """就地解析自然语言日期 due_raw → due_at/planned_date，并从 dict 中剔除该键。

    due_raw 含「前/截止/之前/ddl」视为截止（当天 23:59），否则视为计划日期。
    解析失败静默跳过（保留原字段状态，不瞎猜）。
    """
    raw = values.pop("due_raw", "") or ""
    if not raw:
        return values
    if only_if_unset and (values.get("due_at") is not None or values.get("planned_date") is not None):
        return values
    if not only_if_unset and ("due_at" in values or "planned_date" in values):
        return values
    from app.core.dates import end_of_day, parse_relative_due

    d, _ = parse_relative_due(raw)
    if not d:
        return values
    if any(w in raw for w in ("前", "截止", "之前", "ddl", "DDL")):
        values["due_at"] = end_of_day(d)
    else:
        values["planned_date"] = d
    return values


@router.post("/todos", response_model=TodoOut)
def create_todo(
    body: TodoCreate, current_user: str = Depends(get_current_user)
) -> TodoOut:
    clean = TodoCreate(**_resolve_due_raw(body.model_dump(), only_if_unset=True))
    out = todo_svc.create_todo(clean, user_id=current_user)
    audit(current_user, "todo_created", todo_id=str(out.id))
    return out


@router.get("/todos", response_model=list[TodoOut])
def list_todos(
    current_user: str = Depends(get_current_user),
) -> list[TodoOut]:
    return todo_svc.list_todos(user_id=current_user)


@router.get("/todos/views")
def todo_views(current_user: str = Depends(get_current_user)) -> dict:
    return todo_svc.todo_views(user_id=current_user)


@router.patch("/todos/{todo_id}", response_model=TodoOut)
def update_todo(
    todo_id: str,
    body: TodoUpdate,
    current_user: str = Depends(get_current_user),
) -> TodoOut:
    from uuid import UUID

    try:
        tid = UUID(todo_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="invalid todo id") from e
    payload = _resolve_due_raw(body.model_dump(exclude_unset=True), only_if_unset=False)
    clean = TodoUpdate(**payload)
    out = todo_svc.update_todo(tid, clean, user_id=current_user)
    if not out:
        raise HTTPException(status_code=404, detail="todo not found")
    audit(current_user, "todo_updated", todo_id=str(out.id), status=str(out.status))
    return out


@router.post("/progress")
def add_progress(
    body: ProgressCreate, current_user: str = Depends(get_current_user)
) -> dict:
    from app.core.dates import today as today_d
    from app.models.tables import ProgressEntry, Todo

    with session_scope() as s:
        if body.todo_id:
            todo = s.get(Todo, body.todo_id)
            if not todo or todo.user_id != current_user:
                raise HTTPException(status_code=404, detail="todo not found")
        p = ProgressEntry(
            user_id=current_user,
            work_done=body.work_done,
            work_date=body.work_date or today_d(),
            todo_id=body.todo_id,
            observations=body.observations,
            blockers=body.blockers,
        )
        s.add(p)
        s.flush()
        pid = str(p.id)
    audit(current_user, "progress_added", progress_id=pid)
    return {"id": pid}


@router.get("/progress")
def list_progress(
    limit: int = 50,
    days: int = 7,
    start_date: str = "",
    end_date: str = "",
    current_user: str = Depends(get_current_user),
) -> dict:
    """最近进展记录（倒序）。默认近 7 天；或 start_date/end_date（YYYY-MM-DD）自定义区间。"""
    from datetime import date as date_cls
    from datetime import timedelta

    from sqlmodel import select

    from app.core.dates import today as today_d
    from app.models.tables import ProgressEntry, Todo

    def _parse_date(raw: str) -> date_cls | None:
        try:
            return date_cls.fromisoformat(raw.strip())
        except ValueError:
            return None

    d_start: date_cls | None = _parse_date(start_date)
    d_end: date_cls | None = _parse_date(end_date)
    if d_start is None:
        # 无自定义起点 → 按天数窗口（含今天），上限一年防全表拖库
        window = max(1, min(days, 366))
        d_start = today_d() - timedelta(days=window - 1)
    d_end = d_end or today_d()
    if d_end < d_start:
        d_start, d_end = d_end, d_start

    safe_limit = max(1, min(limit, 500))
    with session_scope() as s:
        rows = s.exec(
            select(ProgressEntry)
            .where(ProgressEntry.user_id == current_user)
            .where(ProgressEntry.work_date >= d_start)
            .where(ProgressEntry.work_date <= d_end)
            .order_by(ProgressEntry.work_date.desc(), ProgressEntry.created_at.desc())
            .limit(safe_limit)
        ).all()
        todo_titles = {
            t.id: t.title
            for t in s.exec(select(Todo).where(Todo.user_id == current_user)).all()
        }
        items = [
            {
                "id": str(p.id),
                "work_date": p.work_date.isoformat(),
                "work_done": p.work_done,
                "observations": p.observations,
                "blockers": p.blockers,
                "todo_id": str(p.todo_id) if p.todo_id else "",
                "todo_title": todo_titles.get(p.todo_id, ""),
                "source_quote": p.source_quote or "",
            }
            for p in rows
        ]
    return {"items": items, "start_date": d_start.isoformat(), "end_date": d_end.isoformat()}


@router.post("/reports/weekly", response_model=ReportOut)
def weekly_report(
    body: ReportRequest, current_user: str = Depends(get_current_user)
) -> ReportOut:
    draft = report_svc.generate_weekly_report(
        user_id=current_user,
        period_start=body.period_start,
        period_end=body.period_end,
    )
    audit(current_user, "report_generated", report_id=str(draft.id))
    return ReportOut(
        id=draft.id,
        period_start=draft.period_start,
        period_end=draft.period_end,
        body_markdown=draft.body_markdown,
        source_refs=draft.source_refs,
        status=draft.status,
    )


@router.get("/reports/{report_id}")
def read_report(
    report_id: str, current_user: str = Depends(get_current_user)
) -> dict:
    info = report_svc.get_report(report_id, user_id=current_user)
    if not info:
        raise HTTPException(status_code=404, detail="report not found")
    return info


@router.patch("/reports/{report_id}")
def patch_report(
    report_id: str,
    body: dict,
    current_user: str = Depends(get_current_user),
) -> dict:
    body_md = body.get("body_markdown")
    if not isinstance(body_md, str) or not body_md.strip():
        raise HTTPException(status_code=422, detail="body_markdown required")
    hash_in = body.get("content_hash") or body.get("expected_hash")
    if not hash_in:
        raise HTTPException(status_code=422, detail="content_hash required")
    r = report_svc.edit_report(
        report_id,
        body_md,
        user_id=current_user,
        expected_hash=str(hash_in),
    )
    if not r.get("ok"):
        code = 404 if r.get("error") == "not_found" else 409
        raise HTTPException(status_code=code, detail=str(r))
    audit(current_user, "report_edited", report_id=report_id)
    return r


@router.get("/chat/history")
def chat_history(
    session_id: str, current_user: str = Depends(get_current_user)
) -> dict:
    """从后端消息记录恢复 UI；附带检查点文本与待审批。"""
    from app.services import chat_store

    msgs = chat_store.list_messages(session_id, user_id=current_user)
    snap = agent.history(session_id=session_id, user_id=current_user)
    # 后端消息优先（含卡片）；检查点仅作补充文本
    if msgs:
        return {
            "messages": [
                {
                    "role": m["role"],
                    "content": m["content"],
                    "card": m.get("card"),
                    "id": m["id"],
                    "created_at": m["created_at"],
                }
                for m in msgs
            ],
            "pending_approval_id": snap.get("pending_approval_id"),
            "source": "db",
        }
    return {
        "messages": snap.get("messages") or [],
        "pending_approval_id": snap.get("pending_approval_id"),
        "source": "checkpoint",
    }


@router.get("/threads")
def list_chat_threads(
    current_user: str = Depends(get_current_user),
) -> dict:
    from app.services import chat_store

    return {"threads": chat_store.list_threads(current_user)}


@router.post("/threads")
def create_chat_thread(
    body: dict, current_user: str = Depends(get_current_user)
) -> dict:
    from app.services import chat_store

    tid = str(body.get("thread_id") or "").strip()
    if not tid:
        from uuid import uuid4

        tid = f"th_{uuid4().hex[:12]}"
    row = chat_store.ensure_thread(
        tid, user_id=current_user, title=str(body.get("title") or "")
    )
    audit(current_user, "thread_created", thread_id=tid)
    return row


@router.post("/threads/{thread_id}/messages")
def save_chat_message(
    thread_id: str,
    body: dict,
    current_user: str = Depends(get_current_user),
) -> dict:
    """前端在每次对话后写入，保证清缓存后可还原。"""
    from app.services import chat_store

    role = str(body.get("role") or "user")
    if role not in ("user", "assistant", "card"):
        raise HTTPException(status_code=422, detail="invalid role")
    msg = chat_store.append_message(
        thread_id,
        role,
        str(body.get("content") or ""),
        user_id=current_user,
        card=body.get("card"),
    )
    return msg


@router.patch("/threads/{thread_id}")
def patch_thread(
    thread_id: str,
    body: dict,
    current_user: str = Depends(get_current_user),
) -> dict:
    from app.services import chat_store

    title = str(body.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=422, detail="title required")
    row = chat_store.rename_thread(thread_id, title, user_id=current_user)
    if not row:
        raise HTTPException(status_code=404, detail="thread not found")
    return row


@router.delete("/threads/{thread_id}")
def del_thread(
    thread_id: str, current_user: str = Depends(get_current_user)
) -> dict:
    from app.services import chat_store

    ok = chat_store.delete_thread(thread_id, user_id=current_user)
    if not ok:
        raise HTTPException(status_code=404, detail="thread not found")
    return {"ok": True, "deleted": thread_id}


@router.get("/stats")
def get_stats(current_user: str = Depends(get_current_user)) -> dict:
    from app.core.stats import snapshot

    return snapshot()


# ---------- 截止提醒 ----------


@router.get("/reminders/settings")
def get_reminder_settings(
    current_user: str = Depends(get_current_user),
) -> dict:
    from app.services.reminders import get_reminder_setting

    return get_reminder_setting(current_user)


@router.put("/reminders/settings")
def put_reminder_settings(
    body: "ReminderSettingBody", current_user: str = Depends(get_current_user)
) -> dict:
    """开启=一次性授权自动发送提醒；关闭后不再自动提醒。"""
    from app.services.reminders import disable_reminder, enable_reminder

    if not body.enabled:
        return disable_reminder(current_user)
    r = enable_reminder(
        current_user,
        to_email=body.to_email,
        hours_before=body.hours_before,
    )
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=str(r))
    audit(current_user, "reminder_setting_updated", enabled=True)
    return r


@router.get("/reminders/due")
def list_due_reminders(
    hours: int | None = None, current_user: str = Depends(get_current_user)
) -> dict:
    """查看提醒窗口内待办（不发邮件）。未传 hours 时用用户设置的提前量。"""
    from app.services.reminders import todos_need_reminder

    items = todos_need_reminder(current_user, hours=hours)
    return {"count": len(items), "items": items}


@router.post("/reminders/run")
def run_reminders(current_user: str = Depends(get_current_user)) -> dict:
    """调试：立即跑一次自动提醒调度。"""
    from app.services.reminders import run_deadline_reminders

    r = run_deadline_reminders(current_user)
    audit(current_user, "reminders_run", **{k: str(v) for k, v in r.items()})
    return r


@router.post("/reminders/check")
def check_reminders(
    body: dict, current_user: str = Depends(get_current_user)
) -> dict:
    """调试：生成提醒邮件**审批单**（与自动通道分离）。"""
    from app.services.reminders import prepare_deadline_reminders

    result = prepare_deadline_reminders(
        user_id=current_user,
        session_id=str(body.get("session_id") or "reminders"),
        to_email=str(body.get("to_email") or ""),
        hours=int(body["hours"]) if body.get("hours") is not None else None,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=str(result))
    audit(current_user, "reminders_checked", created=result.get("created", 0))
    return result


@router.post("/approvals/email")
def prepare_email(
    body: dict, current_user: str = Depends(get_current_user)
) -> dict:
    """直接准备邮件审批单（也可走 Agent 工具）。"""
    required = {"to_email", "subject", "body"}
    if not required.issubset(body):
        raise HTTPException(status_code=422, detail="to_email/subject/body required")
    app = mail_svc.prepare_email(
        user_id=current_user,
        session_id=str(body.get("session_id", "dev")),
        to_email=str(body["to_email"]),
        subject=str(body["subject"]),
        body=str(body["body"]),
        to_name=str(body.get("to_name", "")),
    )
    return {
        "approval_id": str(app.id),
        "status": str(app.status),
        "payload_version": app.payload_version,
    }


@router.get("/approvals/{approval_id}", response_model=ApprovalOut)
def get_approval(
    approval_id: str, current_user: str = Depends(get_current_user)
) -> ApprovalOut:
    from uuid import UUID

    try:
        aid = UUID(approval_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="invalid approval id") from e
    info = mail_svc.get_approval(aid, user_id=current_user)
    if not info:
        raise HTTPException(status_code=404, detail="approval not found")
    return ApprovalOut(
        id=UUID(info["id"]),
        status=info["status"],
        payload=info["payload"],
        payload_version=info["payload_version"],
        exec_result=info["exec_result"],
    )


# ---------- RAG ----------


@router.post("/docs/upload")
async def upload_doc(
    file: UploadFile = File(...),  # noqa: B008  FastAPI 依赖注入
    doc_type: str = "other",
    session_id: str | None = None,
    current_user: str = Depends(get_current_user),
) -> dict:
    """上传 PDF/TXT/MD 入库（递归切分 + embedding）。"""
    import tempfile
    from pathlib import Path

    if session_id:
        from app.services.chat_store import check_thread_access

        check_thread_access(session_id, current_user)
    filename = file.filename or "doc.txt"
    suffix = Path(filename).suffix.lower()
    if suffix not in (".pdf", ".txt", ".md"):
        raise HTTPException(status_code=422, detail="仅支持 PDF、TXT、Markdown 文件")
    content = await file.read(20 * 1024 * 1024 + 1)
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="单个文件不能超过 20MB")
    if not content:
        raise HTTPException(status_code=422, detail="文件为空，请选择有内容的文件")
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        from app.services import rag as rag_svc

        result = await rag_svc.ingest_file(
            tmp_path,
            user_id=current_user,
            doc_type=doc_type,
            original_name=file.filename or "doc.txt",
        )
        if not result.get("document_id") or not result.get("chunks"):
            raise HTTPException(status_code=422, detail="未提取到可检索文字；扫描 PDF 请先进行 OCR")
        if session_id:
            from app.services import chat_store

            chat_store.append_message(session_id, "card", filename, user_id=current_user, card={
                "type": "document", "id": result["document_id"], "title": filename,
                "status": "indexed", "body": "已同步知识库，可围绕这份资料提问。",
            })
        audit(current_user, "doc_uploaded", filename=file.filename or "", **result)
        return result
    except HTTPException:
        raise
    except (UnicodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="文件无法解析，请使用 UTF-8 文本或可读取文字的 PDF") from exc
    except Exception as exc:
        log_event("doc_upload_error", error=str(exc))
        raise HTTPException(status_code=503, detail="资料入库失败，请检查文件或稍后重试") from exc
    finally:
        Path(tmp_path).unlink(missing_ok=True)
        await file.close()


@router.get("/docs")
def list_docs(
    current_user: str = Depends(get_current_user),
) -> list[dict]:
    """已入库文档列表（含分块数），供资料库管理与来源回看。"""
    from sqlalchemy import func

    from app.models.tables import Chunk, Document

    with session_scope() as s:
        rows = (
            s.query(Document, func.count(Chunk.id).label("chunk_count"))
            .outerjoin(Chunk, Chunk.document_id == Document.id)
            .filter(Document.user_id == current_user)
            .group_by(Document.id)
            .order_by(Document.created_at.desc())
            .all()
        )
        return [
            {
                "id": str(doc.id),
                "title": doc.title,
                "filename": doc.filename,
                "doc_type": doc.doc_type,
                "created_at": doc.created_at.isoformat(),
                "chunks": int(chunk_count or 0),
            }
            for doc, chunk_count in rows
        ]


@router.get("/docs/{doc_id}")
def get_doc(
    doc_id: str, current_user: str = Depends(get_current_user)
) -> dict:
    """文档原文（按分块顺序），用于检索引用的原文回看。"""
    from uuid import UUID

    from app.models.tables import Chunk, Document

    try:
        did = UUID(doc_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="invalid document id") from e
    with session_scope() as s:
        doc = s.get(Document, did)
        if not doc or doc.user_id != current_user:
            raise HTTPException(status_code=404, detail="document not found")
        chunks = s.query(Chunk).filter(Chunk.document_id == did).order_by(Chunk.seq.asc()).all()
        return {
            "id": str(doc.id),
            "title": doc.title,
            "filename": doc.filename,
            "doc_type": doc.doc_type,
            "created_at": doc.created_at.isoformat(),
            "chunks": [
                {"seq": c.seq, "page_or_section": c.page_or_section, "content": c.content}
                for c in chunks
            ],
        }


@router.delete("/docs/{doc_id}")
def delete_doc(
    doc_id: str, current_user: str = Depends(get_current_user)
) -> dict:
    """删除文档及其全部分块（不可恢复）。"""
    from uuid import UUID

    from app.models.tables import Chunk, Document

    try:
        did = UUID(doc_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="invalid document id") from e
    with session_scope() as s:
        doc = s.get(Document, did)
        if not doc or doc.user_id != current_user:
            raise HTTPException(status_code=404, detail="document not found")
        n = s.query(Chunk).filter(Chunk.document_id == did).delete()
        s.delete(doc)
        audit(current_user, "doc_deleted", document_id=str(did), chunks=n)
    return {"ok": True, "deleted_chunks": n}

@router.put("/docs/{doc_id}")
def update_doc(
    doc_id: str,
    body: dict,
    current_user: str = Depends(get_current_user),
) -> dict:
    """更新文档标题（仅支持更新标题）。"""
    from uuid import UUID

    from app.models.tables import Document

    try:
        did = UUID(doc_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="invalid document id") from e
    with session_scope() as s:
        doc = s.get(Document, did)
        if not doc or doc.user_id != current_user:
            raise HTTPException(status_code=404, detail="document not found")
        title = (body.get("title") or "").strip()
        if not title:
            raise HTTPException(status_code=422, detail="title required")
        doc.title = title
        s.commit()
        audit(current_user, "doc_renamed", document_id=str(did), title=title)
        return {"ok": True, "title": title}


@router.post("/knowledge/search")
async def knowledge_search(
    body: dict, current_user: str = Depends(get_current_user)
) -> dict:
    """资料库检索：status=ok|no_result，附 citations。"""
    query = str(body.get("query") or "").strip()
    if not query:
        raise HTTPException(status_code=422, detail="query required")
    from app.services import rag as rag_svc

    return await rag_svc.search_knowledge(query, user_id=current_user)
