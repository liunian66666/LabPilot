"""生产鉴权、配置与重启时数据归属不变。"""

from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from app.core.config import Settings
from app.models.db import init_db, session_scope
from app.models.tables import Todo


def settings(**overrides):
    values = {
        "APP_ENV": "production",
        "JWT_SECRET_KEY": "test-independent-0123456789-abcdef",
        "ZOTERO_MCP_URL": "",
    }
    return Settings(_env_file=None, **(values | overrides))


@pytest.mark.parametrize(
    "secret", ["", "short", "dev-insecure-secret-change-me-in-labpilot-0123456789"]
)
def test_production_rejects_unsafe_jwt(secret):
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY") as error:
        settings(JWT_SECRET_KEY=secret).validate_runtime()
    if secret:
        assert secret not in str(error.value)


def test_production_keeps_documented_single_instance_sqlite():
    settings(DATABASE_URL="sqlite:///:memory:").validate_runtime()


def test_zotero_production_requires_encryption_and_internal_auth():
    with pytest.raises(RuntimeError, match="INTEGRATION_SECRET"):
        settings(ZOTERO_MCP_URL="http://zotero-mcp:8001/mcp").validate_runtime()
    key = Fernet.generate_key().decode()
    with pytest.raises(RuntimeError, match="internal token"):
        settings(
            ZOTERO_MCP_URL="http://zotero-mcp:8001/mcp", INTEGRATION_SECRET=key
        ).validate_runtime()
    settings(
        ZOTERO_MCP_URL="http://zotero-mcp:8001/mcp",
        INTEGRATION_SECRET=key,
        ZOTERO_MCP_INTERNAL_TOKEN="test-internal-token-0123456789-abcdef",
    ).validate_runtime()


def test_dev_login_is_removed(anon_client):
    assert (
        anon_client.post(
            "/api/v1/auth/dev-login", json={"username": "root"}
        ).status_code
        == 404
    )


def test_repeated_init_never_reassigns_dev_data():
    title = uuid4().hex
    with session_scope() as session:
        row = Todo(user_id="dev", title=title)
        session.add(row)
        session.flush()
        todo_id = row.id
    init_db()
    init_db()
    with session_scope() as session:
        assert session.get(Todo, todo_id).user_id == "dev"


def test_disabled_legacy_reassignment_does_not_swallow_conflicts():
    from app.models.db import get_engine, migrate_dev_to_root_user

    with pytest.raises(RuntimeError, match="disabled"):
        migrate_dev_to_root_user(get_engine())
