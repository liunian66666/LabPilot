"""数据库引擎与会话。"""

"""
create_engine  → 建立和数据库的连接引擎
      ↓
class X(SQLModel, table=True) → 定义表结构（模型）
      ↓
SQLModel.metadata.create_all(engine) → 按模型建表（init_db 里）
      ↓
with Session(engine) as s: → 开会话，做 CRUD、commit

"""
from collections.abc import Generator
from contextlib import contextmanager

from sqlmodel import Session, SQLModel, create_engine

from app.core.config import get_settings
from app.models.tables import (  # noqa: F401  确保建表
    Approval,
    AuditEvent,
    ChatMessage,
    ChatThread,
    Chunk,
    Document,
    Integration,
    ProgressEntry,
    ReminderLog,
    ReminderSetting,
    ReportDraft,
    Todo,
    TodoEvent,
    User,
    UserPreference,
)

_engine = None
# 迁移成功后才允许提醒调度
_reminder_schema_ready = False
_reminder_schema_error: str | None = None


def reminder_schema_status() -> tuple[bool, str | None]:
    return _reminder_schema_ready, _reminder_schema_error


def get_engine():
    global _engine
    if _engine is None:
        url = get_settings().DATABASE_URL
        kwargs = (
            {"connect_args": {"check_same_thread": False, "timeout": 30}}
            if url.startswith("sqlite")
            else {}
        )
        _engine = create_engine(url, echo=False, **kwargs)
    return _engine


def init_db() -> None:
    global _reminder_schema_ready, _reminder_schema_error
    SQLModel.metadata.create_all(get_engine())
    try:
        migrate_todo_remind_at(get_engine())
        migrate_reminder_logs(get_engine())
        migrate_mail_intents(get_engine())
        _reminder_schema_ready = True
        _reminder_schema_error = None
    except Exception as e:  # noqa: BLE001
        # 不静默、不拖垮整个应用：标记不可用并停用提醒调度
        _reminder_schema_ready = False
        _reminder_schema_error = str(e)
        try:
            from app.core.logging import log_event

            log_event("reminder_schema_migration_failed", error=str(e))
        except Exception:  # noqa: BLE001, S110
            pass


def migrate_todo_remind_at(engine) -> None:
    """旧库补列：todos.remind_at（自定义定时提醒）。additive，幂等。"""
    from sqlalchemy import text

    with engine.begin() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(todos)")).fetchall()]
        if cols and "remind_at" not in cols:
            conn.execute(text("ALTER TABLE todos ADD COLUMN remind_at DATETIME"))
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_todos_remind_at ON todos (remind_at)"
                )
            )


def migrate_mail_intents(engine) -> None:
    """旧审批补持久化唯一标识；保留历史发送记录，只失效重复的待批准单。"""
    from sqlalchemy import inspect, text

    from app.core.mail_intent import intent_key

    with engine.begin() as conn:
        columns = {c["name"] for c in inspect(conn).get_columns("approvals")}
        if "intent_key" not in columns:
            conn.execute(
                text("ALTER TABLE approvals ADD COLUMN intent_key VARCHAR(64)")
            )
        rows = conn.execute(
            text(
                "SELECT id,user_id,session_id,payload_version,status,intent_key FROM approvals ORDER BY created_at,id"
            )
        ).fetchall()
        groups = {}
        rank = {
            "sent": 0,
            "sending": 1,
            "unknown": 2,
            "approved": 3,
            "pending": 4,
            "failed": 5,
            "rejected": 6,
            "expired": 7,
        }
        for row in rows:
            key = intent_key(row[1], row[2], row[3])
            groups.setdefault(key, []).append(row)
        for key, members in groups.items():
            canonical = next((r for r in members if r[5] == key), None)
            if canonical is None:
                legacy = [r for r in members if r[5] is None]
                if not legacy:
                    continue
                canonical = min(legacy, key=lambda r: rank.get(r[4], 99))
                conn.execute(
                    text("UPDATE approvals SET intent_key=:key WHERE id=:id"),
                    {"key": key, "id": canonical[0]},
                )
            for row in members:
                if row[0] != canonical[0] and row[5] is None and row[4] == "pending":
                    conn.execute(
                        text(
                            "UPDATE approvals SET status='expired',exec_result='duplicate intent; use canonical approval' WHERE id=:id AND status='pending'"
                        ),
                        {"id": row[0]},
                    )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_approval_intent ON approvals (intent_key)"
            )
        )


def migrate_reminder_logs(engine) -> None:
    """旧库：去重 + 补唯一索引（create_all 不会改已有表）。"""
    from sqlalchemy import text

    status_rank = {"sent": 0, "sending": 1, "unknown": 2, "failed": 3, "skipped": 4}
    try:
        with engine.begin() as conn:
            rows = conn.execute(
                text("SELECT id, user_id, todo_id, due_key, status FROM reminder_logs")
            ).fetchall()
            best: dict[tuple, tuple] = {}
            ids: list = []
            for r in rows:
                key = (r[1], r[2], r[3])
                rank = status_rank.get(r[4], 9)
                cur = best.get(key)
                if cur is None or rank < cur[0]:
                    if cur is not None:
                        ids.append(cur[1])
                    best[key] = (rank, r[0])
                else:
                    ids.append(r[0])
            for rid in ids:
                conn.execute(
                    text("DELETE FROM reminder_logs WHERE id=:id"), {"id": str(rid)}
                )
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_reminder_due "
                    "ON reminder_logs (user_id, todo_id, due_key)"
                )
            )
    except Exception as e:
        # 不静默：上抛由 init_db 记录并拒绝进入提醒调度
        raise RuntimeError(f"reminder_logs migration failed: {e}") from e


def migrate_dev_to_root_user(engine) -> None:
    """旧的跨用户批量重写已禁用，防止脚本误调用继续转移用户数据。"""
    raise RuntimeError(
        "Automatic user reassignment is disabled; restore verified ownership offline from a backup"
    )


# 把这个生成器函数变成一个**上下文管理器**，于是能用 `with session_scope() as s:` 调用。`yield` 之前的代码在进入 with 时执行，`yield` 处把 session 交给 with 块，with 块结束后继续执行 yield 之后的代码。
# 单纯的生成器函数yield需要用next（）才可以继续执行
@contextmanager
def session_scope() -> Generator[Session, None, None]:
    with Session(get_engine()) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


# def get_session() -> Generator[Session, None, None]:
#     with Session(get_engine()) as session:
#         yield session
