"""离线一致性备份：先停止应用；同时备份业务库与 checkpoint，不覆盖旧目录。"""

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


def snapshot(database: Path, checkpoint: Path, output: Path) -> dict:
    sources = {
        "business.db": database.resolve(),
        "checkpoints.db": checkpoint.resolve(),
    }
    if len(set(sources.values())) != 2:
        raise ValueError("Business and checkpoint must be different files")
    for source in sources.values():
        if not source.is_file():
            raise ValueError("Both source databases must exist")
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"requires_stopped_application": True, "files": {}}
    for name, source in sources.items():
        target = output / name
        with (
            sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as origin,
            sqlite3.connect(target) as destination,
        ):
            origin.backup(destination)
            if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Snapshot integrity check failed")
        manifest["files"][name] = {
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest()
        }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--application-stopped", action="store_true", required=True)
    args = parser.parse_args()
    snapshot(args.database, args.checkpoint, args.output)
    print("Snapshot complete; source files were not changed.")


if __name__ == "__main__":
    main()
