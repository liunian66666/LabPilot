import { createRequire } from 'node:module'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import assert from 'node:assert/strict'
const require = createRequire(import.meta.url)
const { chromium } = require('C:/Users/25690/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')
const output = resolve('../docs/test-results/browser-acceptance')
mkdirSync(output, { recursive: true })
const browser = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
const page = await context.newPage()
const results = []
const errors = []
page.on('pageerror', e => errors.push(e.message))
page.on('dialog', d => d.accept())
async function check(name, fn) {
  try { await fn(); results.push({ name, passed: true }); console.log('PASS', name) }
  catch (e) { results.push({ name, passed: false, error: e.message }); throw e }
}
async function reloadA() {
  await page.reload()
  await page.locator('.report-card').waitFor()
}
try {
  await page.goto('http://127.0.0.1:5189/chat')
  await check('清缓存后从后端恢复会话列表', async () => {
    await page.locator('.session-item').filter({ hasText: '验收会话 A' }).waitFor({ timeout: 7000 })
    await page.locator('.session-item').filter({ hasText: '验收会话 B' }).waitFor()
  })
  await page.locator('.session-item').filter({ hasText: '验收会话 A' }).click()
  await page.locator('.report-card').waitFor()
  await check('同一周报只显示一张卡片且隐藏内部字段', async () => {
    assert.equal(await page.locator('.report-card').count(), 1)
    assert.doesNotMatch(await page.locator('.report-card').innerText(), /\bdraft\b|\bedited\b|[0-9a-f]{8}-[0-9a-f]{4}-/)
  })
  await check('查看周报完整正文', async () => {
    await page.getByRole('button', { name: '查看全文', exact: true }).click()
    await page.locator('.report-body').waitFor()
    assert.match(await page.locator('.report-body').innerText(), /本周|周报/)
  })
  await check('修改草稿刷新后自动恢复', async () => {
    await page.getByRole('button', { name: '编辑周报', exact: true }).click()
    await page.locator('.report-card textarea').fill('# 浏览器验收周报\n\n保留未保存草稿：42')
    await reloadA()
    assert.match(await page.locator('.report-card textarea').inputValue(), /保留未保存草稿：42/)
  })
  await check('切换会话后返回仍恢复未保存草稿', async () => {
    await page.locator('.session-item').filter({ hasText: '验收会话 B' }).click()
    await page.getByText('验收会话 B的原始提问', { exact: true }).waitFor()
    await page.locator('.session-item').filter({ hasText: '验收会话 A' }).click()
    await page.locator('.report-card textarea').waitFor()
    assert.match(await page.locator('.report-card textarea').inputValue(), /保留未保存草稿：42/)
  })
  let reportId
  await check('保存周报写入真实后端', async () => {
    const response = page.waitForResponse(r => r.url().includes('/reports/') && r.request().method() === 'PATCH')
    await page.getByRole('button', { name: '保存修改', exact: true }).click()
    const saved = await (await response).json()
    reportId = saved.id
    await page.getByText('周报已保存。已有邮件草稿不会自动更新，请重新拟信后确认发送。').waitFor()
    const report = await (await context.request.get(`http://127.0.0.1:5189/api/v1/reports/${reportId}`)).json()
    assert.match(report.body_markdown, /保留未保存草稿：42/)
  })
  await check('版本冲突保留输入且不覆盖数据库', async () => {
    await page.getByRole('button', { name: '编辑周报', exact: true }).click()
    await page.locator('.report-card textarea').fill('我的冲突修改')
    const latest = await (await context.request.get(`http://127.0.0.1:5189/api/v1/reports/${reportId}`)).json()
    await context.request.patch(`http://127.0.0.1:5189/api/v1/reports/${reportId}`, { data: { body_markdown: '其他窗口已保存的内容', content_hash: latest.content_hash } })
    await page.getByRole('button', { name: '保存修改', exact: true }).click()
    await page.getByText(/周报已有其他修改，未覆盖/).waitFor()
    assert.equal(await page.locator('.report-card textarea').inputValue(), '我的冲突修改')
    const current = await (await context.request.get(`http://127.0.0.1:5189/api/v1/reports/${reportId}`)).json()
    assert.equal(current.body_markdown, '其他窗口已保存的内容')
    await page.getByRole('button', { name: '取消', exact: true }).click()
  })
  await check('重命名会话刷新后保留', async () => {
    const item = page.locator('.session-item').filter({ hasText: '验收会话 A' })
    await item.hover()
    await item.getByRole('button', { name: '重命名会话' }).click()
    const renamed = page.waitForResponse(r => r.url().endsWith('/threads/browser-A') && r.request().method() === 'PATCH')
    await page.getByRole('textbox', { name: '会话名称' }).fill('浏览器验收自定义标题')
    await page.getByRole('textbox', { name: '会话名称' }).press('Enter')
    assert.equal((await renamed).status(), 200)
    await reloadA()
    await page.locator('.session-item').filter({ hasText: '浏览器验收自定义标题' }).waitFor()
  })
  await check('拒绝邮件后刷新恢复已拒绝与完整正文', async () => {
    const card = page.locator('.biz-card').filter({ hasText: '验收拒绝邮件' })
    await card.getByRole('button', { name: '拒绝', exact: true }).click()
    await card.getByText('已拒绝', { exact: true }).waitFor()
    await reloadA()
    await card.getByText('已拒绝', { exact: true }).waitFor()
    assert.match(await card.innerText(), /第二段内容/)
    assert.equal(await card.getByRole('button', { name: '批准并发送' }).isDisabled(), true)
  })
  await check('批准邮件仅 mock 执行且刷新恢复已发送', async () => {
    const prepared = await (await context.request.post('http://127.0.0.1:5189/api/v1/approvals/email', {
      data: { session_id: 'browser-A', to_email: 'teacher@example.com', subject: '验收批准邮件', body: '浏览器验收完整正文\n第二段内容，仅 mock，不真实发送。' },
    })).json()
    await context.request.post('http://127.0.0.1:5189/api/v1/threads/browser-A/messages', {
      data: { role: 'card', content: '验收批准邮件', card: { type: 'approval', id: prepared.approval_id, title: '验收批准邮件', status: prepared.status, content_hash: prepared.payload_version } },
    })
    await reloadA()
    const card = page.locator('.biz-card').filter({ hasText: '验收批准邮件' })
    const response = page.waitForResponse(r => r.url().endsWith('/chat/resume'))
    await card.getByRole('button', { name: '批准并发送' }).click()
    const result = await (await response).json()
    assert.equal(result.status, 'sent')
    assert.equal(result.executed, true)
    const stored = await (await context.request.get(`http://127.0.0.1:5189/api/v1/approvals/${prepared.approval_id}`)).json()
    assert.match(stored.exec_result, /mock/)
    await reloadA()
    await card.getByText('已发送', { exact: true }).waitFor()
    assert.match(await card.innerText(), /第二段内容/)
  })
  await check('历史消息显示日期与原始时间', async () => {
    assert.match(await page.locator('.ai-time').first().innerText(), /\d{4}/)
  })
  await page.screenshot({ path: resolve(output, 'chat.png'), fullPage: true })
  await check('周报页生成并编辑及返回恢复草稿', async () => {
    await page.goto('http://127.0.0.1:5189/report')
    await page.getByRole('button', { name: '生成草稿', exact: true }).click()
    await page.getByRole('button', { name: '编辑周报', exact: true }).click()
    await page.locator('.report-card textarea').fill('独立周报页未保存草稿')
    await page.goto('http://127.0.0.1:5189/chat')
    await page.goto('http://127.0.0.1:5189/report')
    await page.locator('.report-card textarea').waitFor()
    assert.equal(await page.locator('.report-card textarea').inputValue(), '独立周报页未保存草稿')
  })
  await page.screenshot({ path: resolve(output, 'report-draft.png'), fullPage: true })
  if (process.env.BROWSER_ACCEPTANCE_FAKE_MODEL === '1') {
    await check('正文在生成结束前流式显示且刷新恢复完整回复', async () => {
      await page.goto('http://127.0.0.1:5189/chat')
      await page.waitForLoadState('networkidle')
      await page.getByRole('button', { name: '新会话', exact: true }).click()
      await page.getByPlaceholder('询问任何问题…').fill('流式验收')
      await page.getByRole('button', { name: '发送', exact: true }).click()
      await page.locator('.ai-body').filter({ hasText: '流式验收' }).waitFor()
      assert.equal(await page.getByRole('button', { name: '发送', exact: true }).isDisabled(), true)
      assert.doesNotMatch(await page.locator('.ai-body').last().innerText(), /回复完成/)
      await page.locator('.ai-body').filter({ hasText: '回复完成' }).waitFor()
      await page.locator('.thinking').waitFor({ state: 'hidden' })
      await page.reload()
      await page.locator('.ai-body').filter({ hasText: '流式验收：文字正在逐段显示，回复完成。' }).waitFor()
      await page.waitForLoadState('networkidle')
      await page.screenshot({ path: resolve(output, 'stream.png'), fullPage: true, animations: 'disabled' })
    })
  }
  await check('无浏览器运行异常', async () => assert.deepEqual(errors, []))
} catch (e) {
  console.error(e.message)
  await page.screenshot({ path: resolve(output, 'failure.png'), fullPage: true })
  process.exitCode = 1
} finally {
  writeFileSync(resolve(output, 'results.json'), JSON.stringify({ results, browserErrors: errors, realEmailSent: false, mailBackend: 'mock' }, null, 2))
  await browser.close()
}
