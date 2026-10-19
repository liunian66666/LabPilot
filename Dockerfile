# LabPilot 单镜像多阶段构建：Stage1 构建前端 → Stage2 Python 运行时同端口服务 API + SPA
# syntax=docker/dockerfile:1

# ---------- Stage 1: 前端构建 ----------
FROM node:22-alpine AS frontend-builder
WORKDIR /build
# 依赖层：package-lock 不变则命中缓存
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
# 构建层：源码变更只重建这里
COPY frontend/ ./
RUN npm run build

# ---------- Stage 2: 运行时 ----------
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    TZ=Asia/Shanghai

# tzdata：zoneinfo 解析 Asia/Shanghai 需要；uv 经清华镜像 pip 安装（不走 ghcr）
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv -i https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /app
# 依赖层：uv.lock / pyproject 不变则不重装（最重的层）
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# 代码层
COPY app/ ./app/
COPY --from=frontend-builder /build/dist ./frontend/dist

RUN groupadd --system labpilot \
    && useradd --system --gid labpilot --home /app labpilot \
    && mkdir -p /app/data /app/logs \
    && chown -R labpilot:labpilot /app/data /app/logs

# 数据/日志默认落 /app 下子目录，由 compose volume 持久化
ENV DATABASE_URL="sqlite:////app/data/labpilot.db" \
    CHECKPOINT_DB_PATH="/app/data/labpilot_checkpoints.db"

EXPOSE 8000
USER labpilot
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=3)"]
CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
