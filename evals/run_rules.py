"""确定性规则评测：不依赖 LLM/外网。

用法：
    uv run python -m evals.run_rules
报告口径：样本量、通过数、失败 id；不报告无依据百分比改进。
"""

from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.core.config import get_settings
from app.core.dates import parse_relative_due, today
from app.models.db import init_db
from app.services import extract as extract_svc
from app.services import mail as mail_svc
from app.services import report as report_svc

CASES = Path(__file__).parent / "cases" / "rules.jsonl"


def _load_cases() -> list[dict]:
    rows = []
    for line in CASES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _check_date(case: dict) -> tuple[bool, str]:
    parsed, needs = parse_relative_due(case["input"])
    exp = case["expect"]
    due_exp = exp.get("due_date")
    if due_exp == "TODAY":
        ok = parsed == today()
    elif due_exp == "TOMORROW":
        ok = parsed == today() + timedelta(days=1)
    elif due_exp == "NEXT_FRIDAY":
        # 下周五
        ok = parsed is not None and parsed.weekday() == 4 and parsed > today()
    elif due_exp is None:
        ok = parsed is None
    else:
        ok = parsed is not None and parsed.isoformat() == str(due_exp)
    if needs != exp.get("needs_confirm"):
        return False, f"needs_confirm {needs} != {exp.get('needs_confirm')}"
    return ok, f"got {parsed}"


def _check_extract(case: dict) -> tuple[bool, str]:
    resp = extract_svc.extract_tasks_rule(case["input"])
    exp = case["expect"]
    if len(resp.tasks) < exp.get("min_tasks", 0):
        return False, f"tasks={len(resp.tasks)}"
    if exp.get("quote_contains") and not any(exp["quote_contains"] in t.source_quote for t in resp.tasks):
        return False, "quote missing"
    if exp.get("source") and not any(t.source.value == exp["source"] for t in resp.tasks):
        return False, f"source != {exp['source']}"
    if exp.get("planned_date") == "TODAY" and not any(t.planned_date == today() for t in resp.tasks):
        return False, "planned_date != today"
    return True, f"tasks={len(resp.tasks)}"


def _check_report(case: dict) -> tuple[bool, str]:
    draft = report_svc.generate_weekly_report(user_id="eval")
    body = draft.body_markdown
    exp = case["expect"]
    for token in (
        exp.get("body_contains", [])
        if isinstance(exp.get("body_contains"), list)
        else [exp.get("body_contains")]
    ):
        if token and token not in body:
            return False, f"missing {token}"
    for token in (
        exp.get("must_not_contain", [])
        if isinstance(exp.get("must_not_contain"), list)
        else [exp.get("must_not_contain")]
    ):
        if token and token in body:
            return False, f"fabricated {token}"
    return True, "ok"


def _check_approval(case: dict) -> tuple[bool, str]:
    action = case["input"]["action"]
    exp = case["expect"]
    if action == "reject_then_approve":
        app = mail_svc.prepare_email(
            user_id="eval",
            session_id="eval-ap",
            to_email="t@example.com",
            subject="s",
            body="b-reject",
        )
        r1 = mail_svc.decide_approval(
            user_id="eval",
            approval_id=app.id,
            decision="rejected",
            payload_version_in=app.payload_version,
        )
        app2 = mail_svc.prepare_email(
            user_id="eval",
            session_id="eval-ap",
            to_email="t@example.com",
            subject="s",
            body="b-approve",
        )
        r2 = mail_svc.decide_approval(
            user_id="eval",
            approval_id=app2.id,
            decision="approved",
            payload_version_in=app2.payload_version,
        )
        ok = (
            r1.get("executed") is False
            and r2.get("executed") is True
            and r2.get("status") == "sent"
            and r2.get("result", {}).get("mode") == "mock"
        )
        return ok, f"r1={r1.get('executed')} r2={r2.get('executed')}"
    if action == "bad_version":
        app = mail_svc.prepare_email(
            user_id="eval",
            session_id="eval-ap2",
            to_email="t@example.com",
            subject="s",
            body="b",
        )
        r = mail_svc.decide_approval(
            user_id="eval",
            approval_id=app.id,
            decision="approved",
            payload_version_in="bad",
        )
        return r.get("error") == exp["status"], str(r)
    return False, "unknown action"


def _check_rag(case: dict) -> tuple[bool, str]:
    """直接调用 search_knowledge（与接口同路径）。"""
    from app.models.db import session_scope
    from app.models.tables import Chunk, Document
    from app.services import rag as rag_svc

    q = case["input"]["query"]
    user_id = "eval-" + case["id"]
    corpus = case["input"].get("corpus") or []
    with session_scope() as s:
        doc = Document(
            title="eval", filename="eval.md", doc_type="meeting_note", user_id=user_id
        )
        s.add(doc)
        s.flush()
        for i, text in enumerate(corpus):
            s.add(Chunk(document_id=doc.id, content=text, seq=i, embedding_json=None))

    result = rag_svc.search_knowledge_sync(q, user_id=user_id)
    status = result.get("status")
    return status == case["expect"]["status"], f"status={status}"


HANDLERS = {
    "date_parse": _check_date,
    "task_extract": _check_extract,
    "report_no_fabricate": _check_report,
    "approval_gate": _check_approval,
    "rag_states": _check_rag,
}


def _run_cases() -> None:
    get_settings.cache_clear()
    init_db()
    cases = _load_cases()
    passed = 0
    failed: list[str] = []
    for case in cases:
        fn = HANDLERS.get(case["category"])
        if not fn:
            failed.append(case["id"] + ": no handler")
            continue
        try:
            ok, detail = fn(case)
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"exc {e}"
        if ok:
            passed += 1
            print(f"PASS {case['id']} {detail}")
        else:
            failed.append(case["id"])
            print(f"FAIL {case['id']} {detail}")

    total = len(cases)
    print("\n=== 评测结果 ===")
    print(f"样本量 n={total}  通过={passed}  失败={len(failed)}")
    if failed:
        print("失败 id:", ", ".join(failed))
        raise SystemExit(1)
    print("全部通过（规则集）；模型效果评测另附逐例结果，不在本脚本。")


def main() -> None:
    from app.models import db

    # 即使调用者带了真实 SMTP/模型/业务库配置，也只能操作这份临时库。
    original = (db._engine, db._reminder_schema_ready, db._reminder_schema_error)
    with TemporaryDirectory(prefix="labpilot_rules_") as directory:  # noqa: SIM117 - 先分配目录再覆盖环境
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "development",
                "MAIL_BACKEND": "mock",
                "OPENAI_API_KEY": "",
                "RAG_USE_RERANK": "false",
                "ZOTERO_MCP_URL": "",
                "DATABASE_URL": f"sqlite:///{(Path(directory) / 'rules.db').as_posix()}",
            },
        ):
            db._engine = None
            get_settings.cache_clear()
            try:
                _run_cases()
            finally:
                if db._engine is not None:
                    db._engine.dispose()
                db._engine, db._reminder_schema_ready, db._reminder_schema_error = (
                    original
                )
    get_settings.cache_clear()


if __name__ == "__main__":
    main()
