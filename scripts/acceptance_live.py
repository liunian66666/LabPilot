"""真实模型、独立数据库、真实进程重启验收；强制 mock 邮件和禁用后台提醒。"""

import argparse
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-model", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ | {
        "APP_ENV": "development",
        "DATABASE_URL": f"sqlite:///{(directory / 'business.db').as_posix()}",
        "CHECKPOINT_DB_PATH": str(directory / "checkpoints.db"),
        "MAIL_BACKEND": "mock",
        "REMINDER_SCHEDULER_ENABLED": "false",
        "JWT_SECRET_KEY": secrets.token_urlsafe(48),
        "ZOTERO_MCP_URL": "",
        "CHAT_REQUESTS_PER_MINUTE": "100",
    }
    process = None
    checks = []
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=240)
    log = (directory / "server.log").open("w", encoding="utf-8")

    def start():
        nonlocal process
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=root,
            env=env,
            stdout=log,
            stderr=log,
        )
        for _ in range(150):
            if process.poll() is not None:
                raise RuntimeError("Acceptance server exited; inspect local server.log")
            try:
                if client.get("/ready", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        raise RuntimeError("Acceptance server did not become ready")

    def stop():
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def request(method, path, body=None):
        response = client.request(method, "/api/v1" + path, json=body)
        response.raise_for_status()
        return response.json()

    def passed(name):
        checks.append({"name": name, "passed": True})
        print("PASS", name, flush=True)

    try:
        start()
        auth = request(
            "POST",
            "/auth/register",
            {"username": "acceptance", "password": secrets.token_urlsafe(18)},
        )
        client.headers["Authorization"] = "Bearer " + auth["access_token"]
        tid = "live-acceptance"
        request("POST", "/threads", {"thread_id": tid, "title": "真实模型验收"})
        tasks = request(
            "POST",
            "/notes/extract",
            {"text": "本周五前补完消融实验，结果给老师看。", "persist": True},
        )
        assert tasks["tasks"]
        request(
            "POST",
            "/progress",
            {
                "work_done": "消融实验基线准确率78.3%，已完成两组对照",
                "todo_id": tasks["tasks"][0]["todo_id"],
            },
        )
        passed("纪要任务与进展入库")
        chat = request(
            "POST",
            "/chat",
            {
                "session_id": tid,
                "messages": [
                    {
                        "role": "user",
                        "content": "查一下消融实验相关待办，结合本周进展生成本周周报，先不要拟信。",
                    }
                ],
            },
        )
        reports = [a for a in chat.get("artifacts", []) if a["type"] == "report"]
        assert reports, "Missing report artifact"
        rid = reports[-1]["id"]
        report = request("GET", f"/reports/{rid}")
        assert "78.3" in report["body_markdown"]
        passed("真实模型查任务并生成有依据的周报及结构化卡片")
        full_body = (
            report["body_markdown"]
            + "\n\n人工补充验收：全文传递标记-LABPILOT。\n"
            + "完整正文不可截断。" * 180
        )
        request(
            "PATCH",
            f"/reports/{rid}",
            {"body_markdown": full_body, "expected_hash": report["content_hash"]},
        )
        chat = request(
            "POST",
            "/chat",
            {
                "session_id": tid,
                "active_report_id": rid,
                "messages": [
                    {
                        "role": "user",
                        "content": f"请使用现有周报 report_id={rid} 的修改后全文，准备给 teacher@example.com 的邮件，等待审批；不要重新生成周报。",
                    }
                ],
            },
        )
        aid = chat.get("pending_approval_id")
        assert aid, "Missing approval interrupt"
        approval = request("GET", f"/approvals/{aid}")
        assert approval["payload"]["body"] == full_body, (
            "Email body differs from edited report"
        )
        passed("修改后拟信全文一致并进入人工审批")
        stop()
        start()
        history = request("GET", f"/chat/history?session_id={tid}")
        assert history["messages"] and history["pending_approval_id"] == aid
        passed("真实进程重启恢复历史与待审批")
        decision = {
            "approval_id": aid,
            "decision": "approved",
            "payload_version": approval["payload_version"],
            "session_id": tid,
        }
        result = request("POST", "/chat/resume", decision)
        assert result["status"] == "sent" and result["result"]["mode"] == "mock"
        assert result["graph_resume"] == "图已恢复。", result["graph_resume"]
        repeated = request("POST", "/chat/resume", decision)
        assert repeated["idempotent"]
        with sqlite3.connect(directory / "business.db") as database:
            assert (
                database.execute(
                    "SELECT count(*) FROM approvals WHERE session_id=?", (tid,)
                ).fetchone()[0]
                == 1
            )
        passed("批准仅 mock 执行一次且图恢复未新建审批单")
    except Exception as exc:
        checks.append(
            {"name": "acceptance", "passed": False, "error_type": type(exc).__name__}
        )
        raise
    finally:
        stop()
        client.close()
        log.close()
        (directory / "result.json").write_text(
            json.dumps(
                {"real_model": True, "mail_backend": "mock", "checks": checks},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
