"""SSE bridge: one worker owns actor context for the entire graph run."""

import asyncio
import json
from contextvars import copy_context
from queue import Empty, Full, Queue
from threading import Event, Thread
from time import monotonic

from app.core.config import get_settings
from app.core.llm import IncompleteReplyError
from app.core.logging import log_event
from app.services import chat_store


async def stream_chat_events(agent, body, user_id: str, lease=None):
    from app.core.admission import acquire

    if lease is None:
        lease = acquire(user_id, body.session_id)
    queue = Queue(maxsize=get_settings().CHAT_STREAM_QUEUE_SIZE)
    disconnected = Event()  # 断连标志：客户端是否已离开
    finished = Event()

    def publish(event):
        # 慢客户端触发有界背压；断连后丢弃展示事件，继续保存业务结果。
        deadline = monotonic() + 5
        while not disconnected.is_set():
            try:
                queue.put(event, timeout=0.1)
                return
            except Full:
                if monotonic() >= deadline:
                    disconnected.set()
                    log_event(
                        "chat_stream_slow_consumer",
                        user_id=user_id,
                        session_id=body.session_id,
                    )

    def worker():
        try:
            # 1. 拿用户最新发的那条消息文本
            text = body.messages[-1].content
            # 2. 拼上下文提示：告诉Agent当前正在编辑哪个报告/待办/审批
            hints = [
                f"[{key}={getattr(body, key)}]"
                for key in ("active_report_id", "active_todo_id", "active_approval_id")
                if getattr(body, key)
            ]
            # 3. 从历史消息里把之前上传的文档元数据找出来（只取最近10个，不塞全文省token）
            docs = [
                {
                    "document_id": row["card"]["id"],
                    "filename": row["card"].get("title", ""),
                }
                for row in chat_store.list_messages(body.session_id, user_id=user_id)
                if (row.get("card") or {}).get("type") == "document"
            ]
            if docs:
                hints.append(
                    "本会话上传资料列表（仅元数据）："
                    + json.dumps(docs[-10:], ensure_ascii=False)
                )
            # 4. 把会话标题自动改成用户最新消息的前24个字，不用用户手动起名字
            chat_store.ensure_thread(body.session_id, user_id=user_id, title=text[:24])
            # 5. 把用户这条消息存进会话历史
            chat_store.append_message(body.session_id, "user", text, user_id=user_id)
            # 6. 跑同步的Agent流式生成逻辑，逐事件处理
            for event in agent.chat_stream(
                user_id=user_id,
                session_id=body.session_id,
                user_text=" ".join([*hints, text]),
            ):
                # 如果Agent流程跑完了，拿到最终完整结果
                if event["type"] == "done":
                    result = event["data"]
                    # 把助手的完整回复存进会话历史记录
                    chat_store.append_message(
                        body.session_id, "assistant", result["reply"], user_id=user_id
                    )
                    # 把生成的产物卡片（待办、审批、报告）也存进历史，下次进来能看到
                    for art in result.get("artifacts") or []:
                        chat_store.append_message(
                            body.session_id,
                            "card",
                            art.get("title") or art["type"],
                            user_id=user_id,
                            card=art,
                        )
                        # 把这个流式事件发给异步侧
                publish(event)
        except Exception as exc:  # noqa: BLE001
            # Agent跑崩了，打错误日志，给前端发友好的错误提示
            log_event("chat_stream_error", error=str(exc))
            publish(
                {
                    "type": "error",
                    "text": str(exc)
                    if isinstance(exc, IncompleteReplyError)
                    else "回复生成中断，请稍后重试。",
                }
            )
        finally:
            # 所有事件发完，往队列里塞个None当"流程结束"的信号
            publish(None)
            finished.set()
            lease.release()

    try:
        lease.mark_started()
        Thread(
            target=copy_context().run,
            args=(worker,),
            daemon=True,
            name="labpilot-chat-stream",
        ).start()
    except BaseException:
        lease.release()
        raise
    try:
        while True:
            if finished.is_set() and queue.empty():
                break
            try:
                event = await asyncio.to_thread(queue.get, True, 10)
            except Empty:
                yield ": heartbeat\n\n"
                continue
            if event is None:
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    finally:
        # A closed page must not discard a running business operation or history.
        disconnected.set()
