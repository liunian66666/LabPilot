# LabPilot

**[在线交互预览](https://caohaoran-ai-agent-portfolio.amuseddill34.chatgpt.site/labpilot/)** · 使用固定示例数据，不连接真实模型、邮箱或 Zotero。


课题组科研事务助理（个人项目原型）

**主闭环：** 组会纪要抽任务（带原文依据）→ 四维待办与进展 → 有依据的周报草稿 → 发送前确认。

- 任务可溯源（document / chunk / quote）
- 周报不编造无记录结论（待补充）
- 邮件审批绑定内容版本；拒绝/重复/改稿行为可预期
- 固定评测集约 30 例；真实测量才写指标

开发基线见 [docs/开发文档.md](docs/开发文档.md)。

### Zotero 多用户 MCP 文献检索

每个 LabPilot 账号在「Zotero 文献」页绑定**自己的** Zotero API Key。LabPilot 将凭证加密存入按用户隔离的记录；Agent 和页面检索经同一个 MCP 客户端调用项目内的只读 MCP 服务，再由该服务访问 Zotero 官方 Web API。MCP 服务不配置固定 Zotero 账号，每次只处理本次请求的凭证；未绑定的账号不会看到 Agent 文献检索工具。

在 [Zotero 密钥设置页](https://www.zotero.org/settings/security)为每位用户创建仅有个人文献库**读取权限**的 Key。Zotero 桌面端的文献需先同步到账号，Docker 中无需启动桌面程序；尚未同步的本地条目不能通过 Web API 检索。当前只支持个人文献库，不支持群组库。

**Docker 启动：**在 `.env` 中设置两个互不相同且需长期保存的随机密钥；`INTEGRATION_SECRET` 是 Fernet 密钥，更换后旧绑定无法解密，用户须重新绑定。

```dotenv
# python -c "import secrets; print(secrets.token_urlsafe(48))"
ZOTERO_MCP_INTERNAL_TOKEN=填入生成的内部令牌
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
INTEGRATION_SECRET=填入生成的Fernet密钥
```

```powershell
docker compose -f docker-compose.yml -f docker-compose.zotero.yml up -d --build
```

Compose 会启动 LabPilot 和独立的 MCP 服务，后端通过 `http://zotero-mcp:8765/mcp` 访问它；MCP 端口不发布到宿主机。登录后打开「Zotero 文献」，粘贴自己的只读 Key 即可连接、更换、检索或解绑；用户 ID 由 MCP 服务向 Zotero 验证后自动取得。其他 LabPilot 账号要使用自己的文献库，分别登录并绑定各自的 Key。只使用 `docker compose up` 不会启动该 MCP 服务。

**本机运行：**在同一环境中设置 `ZOTERO_MCP_INTERNAL_TOKEN`，运行 `python -m app.zotero_server`；LabPilot 的 `.env.local` 再设置 `ZOTERO_MCP_URL=http://127.0.0.1:8765/mcp`、相同的内部令牌及 `INTEGRATION_SECRET`。这里使用的是项目内的多用户 MCP 服务，不能用原先固定账号的 `zotero-mcp-server` 命令替代。跨主机部署须为 MCP 连接配置 HTTPS；不要公开 Zotero 本机 API。

## 快速开始（后端）

```powershell
# 依赖（uv）
uv sync --all-groups

# 配置
if (-not (Test-Path .env)) { Copy-Item .env.example .env }  # 保留已有密钥；按需填 OPENAI_API_KEY

# 启动
uv run uvicorn app.main:app --reload --port 8000
# 文档: http://127.0.0.1:8000/docs
# 健康: http://127.0.0.1:8000/health
# Docker: docker compose up -d --build
# Windows 宿主机默认访问 http://127.0.0.1:18000（容器内仍监听 8000）
# 如需改端口：$env:LABPILOT_PORT='18000'; docker compose up -d --build    

```

## 测试与评测

```powershell
uv run pytest tests -q
uv run python -m evals.run_rules   # 规则集 n=13，打印通过/失败 id
uv run ruff check app tests
```

可重复验收（独立库，邮件强制 mock）：

```powershell
uv run python scripts/seed_demo.py --output .acceptance/demo-new
uv run python scripts/acceptance_live.py --real-model --output .acceptance/live-new
npm --prefix frontend run test:smoke
npm --prefix frontend run build
```

真实模型验收会向配置的模型服务发送合成测试文本并产生调用费用。`_acceptance_chat.py` 等旧脚本属于历史脚本，不作为当前验收入口。部署边界、浏览器脚本、生产密钥与备份恢复见 [部署与验收](docs/部署与验收.md)。

## 实测记录（口径：样本见脚本，不作百分比结论）

| 项目 | 结果 |
| --- | --- |
| pytest | 131 passed（2026-10-15，追加旧卷权限升级与只读库就绪检查回归；模拟模型/SMTP，含多用户、预算、并发、断连、对象授权、检查点重建及评测配置隔离） |
| 规则评测 | 13/13；独立临时库，强制 mock 邮件、禁用模型外联 |
| 真实模型链路 `scripts/acceptance_live.py` | 5/5；纪要/进展入库→查任务生成周报→修改后全文拟信→真实进程重启恢复→批准及重复批准仅 mock 执行一次 |
| 浏览器 `frontend/scripts/browser-delivery.mjs` | 5/5；真实登录、编辑保存、刷新、登出重登、无脚本错误；不新增多余空会话 |
| 前端 | SSE/历史/周报卡片脚本通过，build 通过；依赖修复后审计 0 vulnerabilities（当次 npm 审计口径） |
| Docker | 本地镜像构建与非 root/只读文件系统下的独立临时库冒烟验证通过 |
| CI | 工作流已配置，本地等价检查通过；尚无托管 CI 运行结果 |

> [验收详情](docs/test-results/2026-10-15/交付验收.md)，以及实际旧卷升级发现后的[注册修复](docs/test-results/2026-10-15/注册旧卷修复.md)。邮件始终未真实发送；旧规则评测曾因未隔离读取 SMTP 配置，连接阶段 DNS 失败，现已修复。旧真实模型脚本的历史 7/9 记录不代表当前新脚本结果。Zotero 本机 HTTP MCP 协议使用模拟 Zotero API 响应验收；真实 Zotero 账号及 Docker+Zotero 联合端到端仍待验证。

对照评测（T1–T3，`_compare_eval.py`，逐例计数）：

| 任务 | 模式 | 成功 | HTTP | LLM | 工具 | 耗时 |
| --- | --- | --- | --- | --- | --- | --- |
| T1 待办→进展→周报→拟信 | 按钮 | 是 | 5 | 0 | 0 | 0.28s |
| T1 同上 | 对话 | 是 | 2 | 3 | 4 | 16.4s |
| T2 改周报→拟信全文一致 | 按钮 | 是 | 5 | 0 | 0 | 0.18s |
| T2 同上 | 对话 | 是 | 2 | 5 | 5 | 36.8s |
| T3 缺日期纪要→补全 | 按钮 | 是 | 3 | 0 | 0 | 0.24s |
| T3 同上 | 对话 | 是 | 2 | 3 | 2 | 13.4s |

解读：确定性步骤按钮更快、零模型调用；对话用 **1 次业务意图输入** 完成组合，代价是 **2–5 次 LLM + 延迟**。适合「目标模糊 / 要追问改稿」；适合「表单已知字段」仍用按钮。不作提升百分比结论。

## 前端（Vue3）

```powershell
cd frontend
npm install
npm run dev          # http://127.0.0.1:5173 （已代理 /api → :8000）
npm run build        # 产出 dist/
```

页面：四维待办 · 纪要抽任务 · 进展 · 周报 · 资料库 RAG · 发送确认（HITL）。

需先启动后端：

```powershell
uv run uvicorn app.main:app --reload --port 8000
```

## 主要 API（`/api/v1`）

| 路径 | 说明 |
| --- | --- |
| `POST /notes/extract` | 纪要 → 任务候选（含 source_quote） |
| `POST /todos` `GET /todos` `PATCH /todos/{id}` | 四维待办 |
| `GET /todos/views` | today / this_week / overdue / waiting（动态计算） |
| `POST /progress` | 进展记录 |
| `POST /reports/weekly` | 周报草稿 + source_refs |
| `POST /docs/upload` | PDF/TXT/MD 入库（递归切分+embedding） |
| `POST /knowledge/search` | 混合检索 + 出处 citations |
| `POST /approvals/email` | 准备邮件审批单 |
| `POST /chat/resume` | 批准/拒绝（绑定 payload_version） |
| `POST /chat` | Agent 对话（LangGraph） |
| `GET /reminders/due` | 查看临期/逾期待办 |
| `PUT /reminders/settings` | 开启=一次性授权自动提醒；`enabled:false` 关闭 |
| `POST /reminders/run` | 调试：立即跑一次自动提醒 |
| `POST /reminders/check` | 调试：生成**需确认**的提醒审批单（与自动通道分离） |

### 截止提醒规则

- **自助提醒**：开启时确认邮箱与提前量后 **自动发送**（不必逐次审批）；后台逐个检查已开启提醒的用户，每轮完成后等待 60 秒再检查，实际提醒延迟还取决于用户数和邮件服务耗时。
- 截止提醒与自定义定时提醒均按用户自己的待办、收件邮箱与配置执行；关闭提醒后停止发送；单个用户或单类提醒检查异常不阻断其他检查。
- **临时外发**（给老师等）：仍走审批单，逐次确认  
- 跳过已完成/取消；`reminder_logs` 防重复；**改截止时间后**会按新的 due 再提醒

> 个人学习与求职演示用原型，无真实企业客户与使用规模数据。
