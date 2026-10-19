"""卷升级与就绪写探针回归，不操作开发数据库。"""

from pathlib import Path

from app import prepare_volumes


def test_prepare_volume_changes_only_supplied_paths(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    db = data / "business.db"
    db.write_bytes(b"existing database bytes")
    calls = []
    monkeypatch.setattr(
        prepare_volumes.os,
        "chown",
        lambda p, uid, gid, **kw: calls.append((Path(p), uid, gid)),
        raising=False,
    )
    monkeypatch.setattr(prepare_volumes.os, "chmod", lambda *_: None)
    prepare_volumes.prepare_paths([data], 999, 999)
    assert calls == [(data, 999, 999), (db, 999, 999)]
    assert db.read_bytes() == b"existing database bytes"


def test_ready_rejects_readable_but_not_writable_database(anon_client, monkeypatch):
    from app.models import db

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, statement):
            if str(statement).startswith("UPDATE"):
                raise RuntimeError("attempt to write a readonly database")

    class Engine:
        def connect(self):
            return Connection()

    monkeypatch.setattr(db, "get_engine", lambda: Engine())
    assert anon_client.get("/ready").status_code == 503
