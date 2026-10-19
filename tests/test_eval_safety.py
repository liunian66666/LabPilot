"""规则评测不能继承真实 SMTP、模型或开发库。"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_rules_isolates_developer_configuration(tmp_path):
    source = tmp_path / "must-not-change.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE sentinel (value TEXT)")
        connection.execute("INSERT INTO sentinel VALUES ('untouched')")
    before = source.read_bytes()
    result = subprocess.run(
        [sys.executable, "-m", "evals.run_rules"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        check=False,
        timeout=30,
        env=os.environ
        | {
            "DATABASE_URL": f"sqlite:///{source.as_posix()}",
            "MAIL_BACKEND": "smtp",
            "OPENAI_API_KEY": "must-not-be-used",
        },
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert source.read_bytes() == before
