"""pytest 夹具：初始化测试库。"""

import os
import tempfile

import pytest

# 使用独立临时 SQLite，避免污染开发库
_fd, _path = tempfile.mkstemp(prefix="labpilot_test_", suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_path}"
# Test runs must never inherit SMTP from a developer's .env or shell.
os.environ["MAIL_BACKEND"] = "mock"
os.environ["AUTH_REQUESTS_PER_MINUTE"] = "100000"
os.environ["CHAT_REQUESTS_PER_MINUTE"] = "100000"
os.environ["REMINDER_SCHEDULER_ENABLED"] = "false"
os.environ["CHECKPOINT_DB_PATH"] = ":memory:"
# 普通回归不允许意外读取开发者的真实模型凭证。
os.environ["OPENAI_API_KEY"] = "test-not-a-real-key"


@pytest.fixture(autouse=True)
def _setup_db():
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.models.db import init_db

    init_db()
    yield


@pytest.fixture
def anon_client():
    """未携带 token 的客户端。"""
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _get_token(c, username: str, password: str) -> str:
    """注册（已存在则登录）并返回 access_token。"""
    r = c.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password},
    )
    if r.status_code == 409:
        r = c.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
    assert r.status_code in (200, 201), r.text
    return r.json()["access_token"]


@pytest.fixture
def make_client(anon_client):
    """工厂：以指定用户登录并返回带 Bearer 的客户端（可用于多用户隔离）。"""
    def _make(username: str = "dev", password: str = "secret123"):
        token = _get_token(anon_client, username, password)
        anon_client.headers.update({"Authorization": f"Bearer {token}"})
        return anon_client

    return _make


@pytest.fixture
def client(make_client):
    """默认已登录客户端（dev 用户）。"""
    return make_client("dev", "secret123")
