"""创建独立演示库；要求新目录，不接触现有用户数据或外部服务。"""

import argparse
import os
import secrets
import sys
from datetime import timedelta
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ.update(
        DATABASE_URL=f"sqlite:///{(directory / 'business.db').as_posix()}",
        CHECKPOINT_DB_PATH=str(directory / "checkpoints.db"),
        APP_ENV="development",
        MAIL_BACKEND="mock",
        REMINDER_SCHEDULER_ENABLED="false",
    )
    from app.core.dates import now
    from app.core.security import hash_password
    from app.models.db import init_db, session_scope
    from app.models.tables import ProgressEntry, Todo, User
    from app.services.extract import store_document

    init_db()
    password = secrets.token_urlsafe(18)
    with session_scope() as session:
        session.add(User(username="demo", password_hash=hash_password(password)))
        session.add(
            Todo(user_id="demo", title="补完消融实验", due_at=now() + timedelta(days=3))
        )
        session.add(
            ProgressEntry(
                user_id="demo",
                work_done="跑通两组消融基线",
                observations="准确率78.3%，待排查清洗策略",
            )
        )
    store_document(
        "示例组会纪要", "本周五前补完消融实验，结果给老师看。", user_id="demo"
    )
    print(f"Demo data created in {directory}; username=demo; password={password}")
    print(
        "Start with DATABASE_URL and CHECKPOINT_DB_PATH pointing to this directory; use MAIL_BACKEND=mock."
    )


if __name__ == "__main__":
    main()
