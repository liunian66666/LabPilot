"""LabPilot FastAPI 入口。"""

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import router as api_router
from app.api.v1.zotero import router as zotero_router
from app.core.config import get_settings
from app.core.logging import log_event, setup_logging
from app.core.request_context import RequestContextMiddleware
from app.models.db import init_db
from app.services.reminder_scheduler import reminder_loop


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging()
    init_db()
    log_event("application_startup", app="LabPilot")

    task = (
        asyncio.create_task(reminder_loop(), name="reminder-loop")
        if get_settings().REMINDER_SCHEDULER_ENABLED
        else None
    )
    try:
        yield
    finally:
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        log_event("application_shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.APP_NAME,
        version="0.1.0",
        description="课题组科研事务助理 · 任务可溯源 · 四维待办 · 发送前确认",
        lifespan=lifespan,
    )
    app.include_router(api_router, prefix=settings.API_V1_PREFIX)
    app.include_router(zotero_router, prefix=settings.API_V1_PREFIX)
    app.add_middleware(RequestContextMiddleware)

    from fastapi.responses import JSONResponse
    from langgraph.errors import GraphRecursionError

    from app.core.llm import IncompleteReplyError

    @app.exception_handler(IncompleteReplyError)
    async def incomplete_reply(_request: Request, exc: IncompleteReplyError):
        return JSONResponse({"detail": str(exc)}, status_code=503)

    @app.exception_handler(GraphRecursionError)
    async def recursion_limit(_request: Request, _exc: GraphRecursionError):
        return JSONResponse(
            {"detail": "达到执行步数上限，已停止后续操作，请检查已完成记录。"},
            status_code=503,
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict:
        return {"status": "healthy", "app": settings.APP_NAME, "env": settings.APP_ENV}

    @app.get("/ready")
    def ready():
        from sqlalchemy import text

        from app.models.db import get_engine, reminder_schema_status

        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
                # 实际写事务探针；不更新任何用户行，结束后回滚。
                # 仅 SELECT 1 无法发现旧卷属主导致的只读数据库。
                conn.execute(text("UPDATE users SET username=username WHERE 1=0"))
                conn.rollback()
            if not reminder_schema_status()[0]:
                return JSONResponse(
                    {"status": "not_ready", "component": "schema"}, status_code=503
                )
        except Exception:  # noqa: BLE001 - 就绪检查隐藏基础设施细节
            return JSONResponse(
                {"status": "not_ready", "component": "database"}, status_code=503
            )
        return {"status": "ready"}

    # 单镜像部署：存在前端构建产物时，同端口服务 SPA（本地 dev 不受影响，dist 不存在即跳过）
    dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
    if dist.is_dir():
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles

        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.api_route(
            "/{full_path:path}",
            methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            include_in_schema=False,
        )
        def spa(full_path: str, request: Request) -> FileResponse:
            """SPA fallback：命中的静态文件直接返回，其余回 index.html（history 路由）。"""
            req_path = "/" + full_path  # path 参数不含前导斜杠，补齐后再比对 API 前缀
            if (
                request.method not in ("GET", "HEAD")
                or req_path == settings.API_V1_PREFIX
                or req_path.startswith(settings.API_V1_PREFIX + "/")
            ):
                from fastapi import HTTPException

                raise HTTPException(status_code=404)  # API 未命中路由不回退前端
            candidate = (dist / full_path).resolve()
            if full_path and candidate.is_file() and candidate.is_relative_to(dist):
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    # 直接 python app/main.py 时不能用 reload（uvicorn 需要 import string）
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
