"""运行无外网、无业务卷的独立容器，验证默认启动命令和健康检查。"""

import argparse
import json
import subprocess
import time
from uuid import uuid4


def docker(*args):
    return subprocess.run(
        ["docker", *args], check=True, capture_output=True, text=True, timeout=30
    ).stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="labpilot:internship-20261015")
    args = parser.parse_args()
    name = "labpilot-smoke-" + uuid4().hex[:12]
    created = False
    try:
        docker(
            "run",
            "-d",
            "--name",
            name,
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/app/data:rw,mode=1777",
            "--tmpfs",
            "/app/logs:rw,mode=1777",
            "-e",
            "APP_ENV=production",
            "-e",
            "JWT_SECRET_KEY=smoke-only-independent-0123456789-abcdef",
            "-e",
            "MAIL_BACKEND=mock",
            "-e",
            "REMINDER_SCHEDULER_ENABLED=false",
            args.image,
        )
        created = True
        for _ in range(45):
            state = json.loads(docker("inspect", "--format", "{{json .State}}", name))
            if not state["Running"]:
                raise RuntimeError("Smoke container exited before readiness")
            if state.get("Health", {}).get("Status") == "healthy":
                break
            time.sleep(1)
        else:
            raise RuntimeError("Smoke container did not become healthy")
        docker(
            "exec",
            name,
            "/app/.venv/bin/python",
            "-c",
            "import os,urllib.request; assert os.getuid()!=0; assert urllib.request.urlopen('http://127.0.0.1:8000/ready',timeout=3).status==200",
        )
        print(
            "PASS default container command, Docker healthcheck, non-root, isolated writable data"
        )
    finally:
        if created:
            # 仅清理本脚本创建的随机名称临时容器，没有业务挂载卷。
            docker("rm", "-f", name)


if __name__ == "__main__":
    main()
