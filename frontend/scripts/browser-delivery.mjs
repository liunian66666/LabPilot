// Local real-browser smoke test: synthetic data, no LLM, SMTP always mock.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdirSync, openSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { spawn } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import net from 'node:net'

const require = createRequire(import.meta.url)
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const root = resolve(import.meta.dirname, '../..')
const output = resolve(process.env.BROWSER_OUTPUT || `${root}/.acceptance/browser-${Date.now()}`)
mkdirSync(resolve(output, '..'), { recursive: true })
mkdirSync(output)
const port = await new Promise(resolvePort => {
  const socket = net.createServer()
  socket.listen(0, '127.0.0.1', () => { const port = socket.address().port; socket.close(() => resolvePort(port)) })
})
const base = `http://127.0.0.1:${port}`
const log = openSync(`${output}/server.log`, 'w')
const python = process.env.LABPILOT_PYTHON || `${root}/.venv/Scripts/python.exe`
const server = spawn(python, ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(port)], {
  cwd: root, windowsHide: true, stdio: ['ignore', log, log], env: {
    ...process.env, APP_ENV: 'development', MAIL_BACKEND: 'mock', OPENAI_API_KEY: '',
    DATABASE_URL: `sqlite:///${output.replaceAll('\\', '/')}/business.db`,
    CHECKPOINT_DB_PATH: `${output}/checkpoints.db`, REMINDER_SCHEDULER_ENABLED: 'false',
    JWT_SECRET_KEY: randomBytes(48).toString('hex'), ZOTERO_MCP_URL: '',
  },
})
const checks = []
let browser
let token = ''
async function api(method, path, body) {
  const response = await fetch(`${base}/api/v1${path}`, { method,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body ? JSON.stringify(body) : undefined })
  assert.ok(response.ok, `${method} ${path}: ${response.status}`)
  return response.json()
}
function passed(name) { checks.push({ name, passed: true }); console.log('PASS', name) }
try {
  let ready = false
  for (let i = 0; i < 150; i++) {
    if (server.exitCode !== null) throw new Error('Acceptance server exited')
    try { ready = (await fetch(`${base}/ready`)).ok } catch {}
    if (ready) break
    await new Promise(r => setTimeout(r, 200))
  }
  assert.ok(ready, 'Server not ready')
  const password = randomBytes(18).toString('hex')
  token = (await api('POST', '/auth/register', { username: 'browser_demo', password })).access_token
  const tid = 'browser-delivery-thread'
  await api('POST', '/threads', { thread_id: tid, title: '浏览器交付验收' })
  await api('POST', '/progress', { work_done: '消融实验完成两组基线' })
  const report = await api('POST', '/reports/weekly', {})
  await api('POST', `/threads/${tid}/messages`, { role: 'assistant', content: '已生成周报，等待你修改。' })
  await api('POST', `/threads/${tid}/messages`, { role: 'card', content: '周报草稿', card: { type: 'report', id: report.id, title: '周报草稿' } })
  browser = await chromium.launch({ headless: true, ...(process.env.CHROME_EXECUTABLE ? { executablePath: process.env.CHROME_EXECUTABLE } : {}) })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const page = await context.newPage()
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('dialog', dialog => dialog.accept())
  async function login() {
    await page.goto(`${base}/login`)
    await page.getByPlaceholder('至少 3 个字符').fill('browser_demo')
    await page.getByPlaceholder('至少 6 位').fill(password)
    await page.locator('.login-btn').click()
    await page.locator('.session-item').filter({ hasText: '浏览器交付验收' }).waitFor()
    await page.locator('.session-item').filter({ hasText: '浏览器交付验收' }).click()
    await page.locator('.report-card').waitFor()
  }
  await login()
  assert.equal(await page.locator('.session-item').count(), 1)
  passed('真实浏览器登录并恢复服务端历史')
  await page.getByRole('button', { name: '编辑周报', exact: true }).click()
  await page.locator('.report-card textarea').fill('# 浏览器验收\n\n修改后全文标记：BROWSER-20261015')
  await page.getByRole('button', { name: '保存修改', exact: true }).click()
  await page.getByText('周报已保存。', { exact: false }).waitFor()
  assert.match((await api('GET', `/reports/${report.id}`)).body_markdown, /BROWSER-20261015/)
  passed('浏览器修改周报并验证数据库全文')
  await page.reload()
  await page.locator('.report-card').waitFor()
  assert.equal(await page.locator('.report-card').count(), 1)
  await page.getByRole('button', { name: '查看全文', exact: true }).click()
  await page.locator('.report-body').getByText('修改后全文标记：BROWSER-20261015').waitFor()
  passed('刷新后卡片不重复且修改后全文保留')
  await page.getByRole('button', { name: '退出', exact: true }).click()
  await login()
  assert.equal(await page.locator('.report-card').count(), 1)
  assert.equal(await page.locator('.session-item').count(), 1)
  passed('登出再登录恢复原会话')
  // 真实 HTTP mock 邮件，覆盖聊天卡片与审批页，不只是检查渲染文本。
  let decisions = 0
  await page.route('**/api/v1/chat/resume', async route => {
    decisions++
    await new Promise(resolveDelay => setTimeout(resolveDelay, 200))
    await route.continue()
  })
  const mailPayload = { session_id: tid, to_email: 'teacher@example.com', subject: '防重验收', body: '固定测试正文' }
  const proposed = await api('POST', '/approvals/email', mailPayload)
  for (let i = 0; i < 2; i++) await api('POST', `/threads/${tid}/messages`, {
    role: 'card', content: '邮件草稿', card: { type: 'approval', id: proposed.approval_id, status: 'pending', content_hash: proposed.payload_version },
  })
  await api('POST', '/threads', { thread_id: 'other-thread', title: '其他会话' })
  async function openOriginal() {
    await page.locator('.session-item').filter({ hasText: '浏览器交付验收' }).click()
    await page.locator('.report-card').waitFor()
  }
  async function doubleConfirm() {
    const button = await page.getByRole('button', { name: '批准并发送', exact: true }).elementHandle()
    const completed = page.waitForResponse(response => response.url().endsWith('/chat/resume'))
    await button.evaluate(element => {
      element.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      element.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    assert.equal((await completed).status(), 200)
  }
  async function assertChatSent() {
    await page.locator('.biz-card .tag').filter({ hasText: '已发送' }).waitFor()
    assert.equal(await page.getByRole('button', { name: '批准并发送', exact: true }).isDisabled(), true)
    assert.equal(await page.locator('.biz-card').count(), 1)
  }
  await page.reload()
  await openOriginal()
  await page.getByRole('button', { name: '批准并发送', exact: true }).waitFor()
  await doubleConfirm()
  await assertChatSent()
  assert.equal(decisions, 1)
  await page.reload()
  await assertChatSent()
  await page.locator('.session-item').filter({ hasText: '其他会话' }).click()
  await openOriginal()
  await assertChatSent()
  await page.getByRole('button', { name: '退出', exact: true }).click()
  await login()
  await assertChatSent()
  assert.equal(decisions, 1, 'history restoration must never POST a send')
  const reused = await api('POST', '/approvals/email', mailPayload)
  assert.equal(reused.approval_id, proposed.approval_id)
  assert.equal(reused.status, 'sent')
  passed('聊天邮件连点只提交一次，刷新切会话重登录不重发且同内容不新建单')

  await page.goto(`${base}/approvals`)
  await page.locator('input[type=email]').fill('teacher@example.com')
  await page.locator('.grid-2 input').nth(1).fill('审批页防重验收')
  await page.locator('textarea').fill('审批页固定正文')
  await page.getByRole('button', { name: '生成审批单', exact: true }).click()
  await page.getByText('请核对下面的邮件内容，再确认发送。', { exact: true }).waitFor()
  await doubleConfirm()
  await page.getByRole('button', { name: '已发送', exact: true }).waitFor()
  assert.equal(await page.getByRole('button', { name: '已发送', exact: true }).isDisabled(), true)
  assert.equal(decisions, 2)
  await page.reload()
  await page.getByRole('button', { name: '已发送', exact: true }).waitFor()
  assert.equal(await page.getByRole('button', { name: '已发送', exact: true }).isDisabled(), true)
  await page.getByRole('button', { name: '生成审批单', exact: true }).click()
  await page.getByText('已恢复同内容邮件的处理状态，没有新建或重复发送。', { exact: true }).waitFor()
  assert.equal(decisions, 2)
  passed('审批页状态同步持久恢复，连续点击和重新生成同内容不重发')

  await page.route('**/api/v1/approvals/*', route => route.abort())
  await page.reload()
  await page.getByText('邮件状态核对失败，请刷新状态后再操作。', { exact: true }).waitFor()
  assert.equal(await page.getByRole('button', { name: '批准并发送', exact: true }).isDisabled(), true)
  assert.equal(decisions, 2)
  passed('状态查询失败时禁止发送，不信任缓存里的 pending')
  assert.deepEqual(errors, [])
  await page.screenshot({ path: `${output}/chat.png`, fullPage: true })
  passed('无浏览器运行时错误')
} catch (error) {
  checks.push({ name: 'browser delivery', passed: false, error: error.message })
  throw error
} finally {
  if (browser) await browser.close()
  server.kill()
  await new Promise(resolveExit => { if (server.exitCode !== null) resolveExit(); else server.once('exit', resolveExit) })
  writeFileSync(`${output}/result.json`, JSON.stringify({ realBrowser: true, realModel: false, mailBackend: 'mock', checks }, null, 2))
}
