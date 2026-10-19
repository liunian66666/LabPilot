"""多用户 Zotero 绑定：密文、隔离、权限和 MCP 故障边界。"""

from cryptography.fernet import Fernet
from sqlmodel import select

from app.core.config import get_settings
from app.models.db import session_scope
from app.models.tables import Integration
from app.services.zotero_credentials import has_credential, load_credential


def _set_secret(monkeypatch) -> None:
    monkeypatch.setenv("INTEGRATION_SECRET", Fernet.generate_key().decode("ascii"))
    get_settings.cache_clear()


def _token(make_client, username: str) -> str:
    client = make_client(username, "secret123")
    return client.headers["Authorization"]


def test_credentials_are_encrypted_and_isolated(monkeypatch, make_client):
    _set_secret(monkeypatch)

    async def verify(api_key: str) -> dict:
        return {"user_id": {"alice-secret": 111, "bob-secret": 222}[api_key]}

    async def search(query: str, user_id: str) -> list[dict]:
        assert query == "graph"
        cred = load_credential(user_id)
        return [{"key": cred.zotero_user_id, "title": user_id}]

    monkeypatch.setattr("app.services.zotero_mcp.verify_zotero_credentials", verify)
    monkeypatch.setattr("app.services.zotero_mcp.search_reference_items", search)

    alice = _token(make_client, "zotero_alice")
    bob = _token(make_client, "zotero_bob")
    client = make_client("zotero_alice")

    for token, key, expected in ((alice, "alice-secret", "111"), (bob, "bob-secret", "222")):
        response = client.post(
            "/api/v1/zotero/credential",
            json={"api_key": key},
            headers={"Authorization": token},
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"connected": True, "zotero_user_id": expected}
        assert key not in response.text

    with session_scope() as session:
        rows = session.exec(select(Integration).where(Integration.provider == "zotero")).all()
        assert len([r for r in rows if r.user_id in ("zotero_alice", "zotero_bob")]) == 2
        alice_row = next(r for r in rows if r.user_id == "zotero_alice")
        bob_row = next(r for r in rows if r.user_id == "zotero_bob")
        assert "alice-secret" not in alice_row.api_key_enc
        assert "bob-secret" not in bob_row.api_key_enc
        assert alice_row.api_key_enc != bob_row.api_key_enc

    for token, expected in ((alice, "111"), (bob, "222")):
        status = client.get("/api/v1/zotero/credential", headers={"Authorization": token})
        assert status.status_code == 200
        assert status.json() == {"connected": True, "zotero_user_id": expected}
        result = client.get("/api/v1/zotero/search?q=graph", headers={"Authorization": token})
        assert result.status_code == 200
        assert result.json()["items"][0]["key"] == expected

    deleted = client.delete("/api/v1/zotero/credential", headers={"Authorization": alice})
    assert deleted.status_code == 200
    assert not has_credential("zotero_alice")
    assert has_credential("zotero_bob")
    assert client.get("/api/v1/zotero/search?q=graph", headers={"Authorization": alice}).status_code == 409


def test_bad_key_does_not_replace_existing_binding(monkeypatch, make_client):
    _set_secret(monkeypatch)

    async def verify(api_key: str) -> dict:
        if api_key == "bad-key":
            raise ValueError("sensitive key must not appear in response")
        return {"user_id": 333}

    monkeypatch.setattr("app.services.zotero_mcp.verify_zotero_credentials", verify)
    client = make_client("zotero_replace")
    url = "/api/v1/zotero/credential"
    assert client.post(url, json={"api_key": "good-key"}).status_code == 200
    response = client.post(url, json={"api_key": "bad-key"})
    assert response.status_code == 422
    assert "sensitive key" not in response.text
    assert load_credential("zotero_replace").api_key == "good-key"


def test_invalid_secret_fails_closed(monkeypatch, make_client):
    _set_secret(monkeypatch)

    async def verify(_api_key: str) -> dict:
        return {"user_id": 444}

    monkeypatch.setattr("app.services.zotero_mcp.verify_zotero_credentials", verify)
    client = make_client("zotero_rotate")
    assert client.post("/api/v1/zotero/credential", json={"api_key": "rotation-key"}).status_code == 200
    monkeypatch.setenv("INTEGRATION_SECRET", Fernet.generate_key().decode("ascii"))
    get_settings.cache_clear()
    assert not has_credential("zotero_rotate")
    assert client.get("/api/v1/zotero/credential").status_code == 503


def test_missing_secret_rejects_before_mcp(monkeypatch, make_client):
    monkeypatch.setenv("INTEGRATION_SECRET", "")
    get_settings.cache_clear()

    async def should_not_verify(_api_key: str) -> dict:
        raise AssertionError("MCP should not receive a key that cannot be saved safely")

    monkeypatch.setattr("app.services.zotero_mcp.verify_zotero_credentials", should_not_verify)
    client = make_client("zotero_no_secret")
    response = client.post("/api/v1/zotero/credential", json={"api_key": "private-key"})
    assert response.status_code == 503
    assert "private-key" not in response.text


def test_search_failure_does_not_echo_key(monkeypatch, make_client):
    _set_secret(monkeypatch)

    async def verify(_api_key: str) -> dict:
        return {"user_id": 555}

    async def unavailable(_query: str, _user_id: str) -> list[dict]:
        raise RuntimeError("backend accidentally mentioned private-key")

    monkeypatch.setattr("app.services.zotero_mcp.verify_zotero_credentials", verify)
    monkeypatch.setattr("app.services.zotero_mcp.search_reference_items", unavailable)
    client = make_client("zotero_outage")
    assert client.post("/api/v1/zotero/credential", json={"api_key": "private-key"}).status_code == 200
    response = client.get("/api/v1/zotero/search?q=graph")
    assert response.status_code == 503
    assert "private-key" not in response.text


def test_zotero_routes_require_login(anon_client):
    assert anon_client.get("/api/v1/zotero/credential").status_code == 401
    assert anon_client.post("/api/v1/zotero/credential", json={"api_key": "x"}).status_code == 401
    assert anon_client.delete("/api/v1/zotero/credential").status_code == 401
    assert anon_client.get("/api/v1/zotero/search?q=graph").status_code == 401
