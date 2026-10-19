"""登录与鉴权：注册、登录、JWT 校验、用户隔离、密码哈希。"""

import time

import jwt as pyjwt
from sqlmodel import select

from app.core.config import get_settings
from app.models.db import session_scope
from app.models.tables import User


def test_register_ok(anon_client):
    r = anon_client.post(
        "/api/v1/auth/register",
        json={"username": "newbie", "password": "secret123"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["access_token"]
    assert body["username"] == "newbie"
    assert body["token_type"] == "bearer"
    # 响应不得回传密码或哈希
    assert "password" not in body and "password_hash" not in body


def test_register_duplicate_409(make_client, anon_client):
    make_client("dupe", "secret123")
    r = anon_client.post(
        "/api/v1/auth/register",
        json={"username": "dupe", "password": "another456"},
    )
    assert r.status_code == 409


def test_register_validation(anon_client):
    # 用户名过短
    r1 = anon_client.post(
        "/api/v1/auth/register", json={"username": "ab", "password": "secret123"}
    )
    assert r1.status_code == 422
    # 密码过短
    r2 = anon_client.post(
        "/api/v1/auth/register", json={"username": "validname", "password": "123"}
    )
    assert r2.status_code == 422


def test_login_ok(make_client, anon_client):
    make_client("logme", "secret123")
    r = anon_client.post(
        "/api/v1/auth/login",
        json={"username": "logme", "password": "secret123"},
    )
    assert r.status_code == 200
    assert r.json()["access_token"]


def test_login_wrong_password_401(make_client, anon_client):
    make_client("wrongpw", "secret123")
    r = anon_client.post(
        "/api/v1/auth/login",
        json={"username": "wrongpw", "password": "incorrect999"},
    )
    assert r.status_code == 401


def test_login_unknown_user_401(anon_client):
    r = anon_client.post(
        "/api/v1/auth/login",
        json={"username": "ghost", "password": "secret123"},
    )
    assert r.status_code == 401


def test_protected_route_without_token_401(anon_client):
    r = anon_client.get("/api/v1/todos")
    assert r.status_code == 401


def test_protected_route_with_garbage_token_401(anon_client):
    r = anon_client.get(
        "/api/v1/todos", headers={"Authorization": "Bearer not.a.valid.jwt"}
    )
    assert r.status_code == 401


def test_protected_route_with_expired_token_401(anon_client):
    settings = get_settings()
    token = pyjwt.encode(
        {"sub": "dev", "exp": int(time.time()) - 10},
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    r = anon_client.get(
        "/api/v1/todos", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 401


def test_token_signed_with_other_secret_401(anon_client):
    token = pyjwt.encode(
        {"sub": "dev", "exp": int(time.time()) + 3600},
        "some-other-secret-0123456789-abcdef",
        algorithm="HS256",
    )
    r = anon_client.get(
        "/api/v1/todos", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 401


def test_password_is_bcrypt_hashed(make_client):
    make_client("hashuser", "mypassword")
    with session_scope() as s:
        u = s.exec(select(User).where(User.username == "hashuser")).first()
        assert u is not None
        assert u.password_hash != "mypassword"
        assert u.password_hash.startswith("$2")  # bcrypt 标识


def test_user_data_isolation(make_client):
    alice = make_client("alice", "secret123")
    r = alice.post("/api/v1/todos", json={"title": "alice-private-task"})
    assert r.status_code == 200
    # 切换为 bob（同一客户端覆盖 Authorization）
    bob = make_client("bob", "secret123")
    todos = bob.get("/api/v1/todos").json()
    assert all(t["title"] != "alice-private-task" for t in todos)
