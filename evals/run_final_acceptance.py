"""Isolated live-model acceptance; mail is always mocked.

Run with the project's Python: python -m evals.run_final_acceptance
Only synthetic fixtures are sent to the configured model. Production databases
are not used. Results are saved under docs/test-results/.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def main():
    project = Path(__file__).resolve().parents[1]
    isolated = Path(tempfile.mkdtemp(prefix="labpilot_final_acceptance_"))
    os.environ["DATABASE_URL"] = "sqlite:///" + (isolated / "business.db").as_posix()
    os.environ["CHECKPOINT_DB_PATH"] = str(isolated / "checkpoints.db")
    os.environ["MAIL_BACKEND"] = "mock"
    os.environ["RAG_USE_RERANK"] = "false"
    os.environ["PYTHONIOENCODING"] = "utf-8"

    from fastapi.testclient import TestClient
    from sqlmodel import select

    from app.agent import graph
    from app.core.config import get_settings
    from app.main import app
    from app.models.db import session_scope
    from app.models.tables import Approval, ReportDraft

    settings = get_settings()
    results = []
    transcript = []
    thread = "final-acceptance"
    original_model = graph.get_chat_model
    graph.get_chat_model = lambda: original_model(timeout=60, max_retries=0)
    original_config = graph.agent._config
    graph.agent._config = lambda sid: {**original_config(sid), "recursion_limit": 12}

    def check(name, passed, detail=None):
        row = {"name": name, "passed": bool(passed), "detail": detail}
        results.append(row)
        print(("PASS " if passed else "FAIL ") + name, flush=True)

    def error_text(exc):
        text = f"{type(exc).__name__}: {exc}"
        if settings.OPENAI_API_KEY:
            text = text.replace(settings.OPENAI_API_KEY, "[REDACTED]")
        return text[:1200]

    def approval_count():
        with session_scope() as session:
            return len(session.exec(select(Approval)).all())

    def chat(client, text, report_id=None):
        started = time.monotonic()
        payload = {"session_id": thread, "messages": [{"role": "user", "content": text}]}
        if report_id:
            payload["active_report_id"] = report_id
        try:
            response = client.post("/api/v1/chat", json=payload)
            data = response.json()
            transcript.append({"user": text, "response": data, "seconds": round(time.monotonic() - started, 2)})
            check("chat_http_200", response.status_code == 200, response.status_code)
            return data
        except Exception as exc:
            check("chat_request", False, error_text(exc))
            return {}

    try:
        with TestClient(app) as client:
            client.post("/api/v1/todos", json={"title": "补完消融实验", "priority": "important", "source": "advisor"})
            client.post("/api/v1/progress", json={"work_done": "跑通消融两组基线", "observations": "测试数据：准确率78.3%，低于预期"})
            if not settings.OPENAI_API_KEY:
                check("live_model_configured", False, "No model credentials configured")
            else:
                first = chat(client, "查一下消融实验相关待办，结合本周进展写周报，最后准备一封给老师的邮件。")
                with session_scope() as session:
                    drafts = session.exec(select(ReportDraft).order_by(ReportDraft.created_at.desc())).all()
                    rid = str(drafts[0].id) if drafts else None
                    count = len(drafts)
                check("agent_created_report", bool(rid), count)
                check("report_artifact_returned", any(a.get("type") == "report" for a in first.get("artifacts", [])))
                check("missing_recipient_not_invented", not first.get("pending_approval_id") and approval_count() == 0,
                      {"pending": first.get("pending_approval_id"), "reply": first.get("reply", "")[:800]})

                if rid and not first.get("pending_approval_id"):
                    original = client.get(f"/api/v1/reports/{rid}").json()
                    marker = "结果下降，需要重新检查数据划分"
                    edited = chat(client, f"修改刚才的周报，在问题与阻塞部分补充“{marker}”。请实际保存到原草稿，保留其他内容，不创建新周报，不准备邮件。", rid)
                    current = client.get(f"/api/v1/reports/{rid}").json()
                    check("agent_edit_saved", marker in current.get("body_markdown", "") and current.get("content_hash") != original.get("content_hash"))
                    check("edited_report_artifact", any(a.get("type") == "report" and a.get("id") == rid for a in edited.get("artifacts", [])))
                    prepared = chat(client, "老师邮箱是teacher@example.com。请用prepare_email_tool的report_id参数读取当前周报全文，准备邮件并等待人工确认，不要发送。", rid)
                    aid = prepared.get("pending_approval_id")
                    check("email_pending_approval", bool(aid))
                    if aid:
                        detail = client.get(f"/api/v1/approvals/{aid}").json()
                        check("email_uses_latest_full_report", detail.get("payload", {}).get("body") == current.get("body_markdown"))
                        state = graph.agent._graph.get_state(graph.agent._config(thread))
                        check("graph_actually_paused", bool(state.next) and bool(state.tasks), list(state.next))
                        recover = """
import json
from app.agent.graph import agent
from app.models.db import init_db
init_db()
h=agent.history(session_id='final-acceptance',user_id='dev')
s=agent._graph.get_state(agent._config('final-acceptance'))
print('RECOVERY_JSON:'+json.dumps({'messages':len(h.get('messages',[])),'pending':h.get('pending_approval_id'),'next':list(s.next)}))
"""
                        child = subprocess.run([sys.executable, "-c", recover], cwd=project, capture_output=True, text=True, encoding="utf-8", timeout=30)
                        line = next((line for line in child.stdout.splitlines() if line.startswith("RECOVERY_JSON:")), None)
                        recovered = json.loads(line[len("RECOVERY_JSON:"):]) if line else {}
                        check("new_process_recovers_history_and_pending", child.returncode == 0 and recovered.get("messages", 0) > 0 and recovered.get("pending") == aid and bool(recovered.get("next")), recovered)
                        count_before = approval_count()
                        reject = client.post("/api/v1/chat/resume", json={"approval_id": aid, "decision": "rejected", "payload_version": detail["payload_version"], "session_id": thread})
                        rejection = reject.json()
                        check("reject_does_not_send", reject.status_code == 200 and rejection.get("executed") is False, rejection)
                        check("reject_resume_does_not_create_duplicate", approval_count() == count_before, {"before": count_before, "after": approval_count()})
                        prepared_again = chat(client, "我现在明确要求重新准备刚才那份周报邮件，收件人仍是teacher@example.com。请使用report_id读取完整周报，force_new=true，新建审批单并等待批准。", rid)
                        new_id = prepared_again.get("pending_approval_id")
                        check("explicit_retry_creates_new_approval", bool(new_id) and new_id != aid, {"old": aid, "new": new_id})
                        if new_id:
                            new_detail = client.get(f"/api/v1/approvals/{new_id}").json()
                            count_before = approval_count()
                            decision = {"approval_id": new_id, "decision": "approved", "payload_version": new_detail["payload_version"], "session_id": thread}
                            approved = client.post("/api/v1/chat/resume", json=decision)
                            approval_result = approved.json()
                            check("approve_executes_mock", approved.status_code == 200 and approval_result.get("executed") is True and approval_result.get("result", {}).get("mode") == "mock", approval_result)
                            check("approve_resume_does_not_create_duplicate", approval_count() == count_before, {"before": count_before, "after": approval_count()})
                            with session_scope() as session:
                                sent_before = len(session.exec(select(Approval).where(Approval.status == "sent")).all())
                            repeated = client.post("/api/v1/chat/resume", json=decision).json()
                            with session_scope() as session:
                                sent_after = len(session.exec(select(Approval).where(Approval.status == "sent")).all())
                            check("repeat_approval_does_not_send", repeated.get("executed") is not True and sent_after == sent_before, repeated)
                            hist = client.get("/api/v1/chat/history", params={"session_id": thread}).json()
                            check("history_available_after_flow", len(hist.get("messages", [])) > 0)
    except Exception as exc:
        check("acceptance_suite_error", False, error_text(exc))
    finally:
        output_dir = project / "docs" / "test-results"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / "final-acceptance.json"
        report = {"model": settings.DEFAULT_LLM_MODEL, "mail_backend": "mock", "isolated_directory": str(isolated), "checks": results, "transcript": transcript}
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"RESULT_FILE: {output}", flush=True)
        print(f"SUMMARY: {sum(r['passed'] for r in results)}/{len(results)}", flush=True)
    return 1 if any(not r["passed"] for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
