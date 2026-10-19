import { createRequire } from 'node:module'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import assert from 'node:assert/strict'
const require = createRequire(import.meta.url)
const { chromium } = require('C:/Users/25690/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')
const output = resolve('../docs/test-results/chat-upload')
mkdirSync(output, { recursive: true })
const browser = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
const page = await context.newPage()
const results = [], errors = []
page.on('pageerror', error => errors.push(error.message))
async function check(name, fn) {
  try { await fn(); results.push({ name, passed: true }); console.log('PASS', name) }
  catch (error) { results.push({ name, passed: false, error: error.message }); throw error }
}
async function drop(files) {
  const data = await page.evaluateHandle(files => {
    const transfer = new DataTransfer()
    for (const { name, body } of files) transfer.items.add(new File([body], name, { type: 'text/plain' }))
    return transfer
  }, files)
  await page.locator('.chat-upload').dispatchEvent('dragenter', { dataTransfer: data })
  await page.locator('.drop-hint').waitFor()
  await page.locator('.chat-upload').dispatchEvent('drop', { dataTransfer: data })
  await data.dispose()
}
let documentId
try {
  await page.goto('http://127.0.0.1:5189/chat')
  await page.locator('.session-item').filter({ hasText: '验收会话 A' }).click()
  await page.locator('.report-card').waitFor()
  await check('拖入文件完成实际上传与知识库同步', async () => {
    const response = page.waitForResponse(r => r.url().includes('/docs/upload') && r.request().method() === 'POST')
    await drop([{ name: '拖拽消融验收.md', body: '消融实验唯一结论：准确率91.7%，截止2030-01-09。' }])
    const result = await (await response).json()
    documentId = result.document_id
    assert.ok(documentId && result.chunks > 0)
    await page.locator('.uploads .success').waitFor()
    await page.locator('.biz-card').filter({ hasText: '拖拽消融验收.md' }).waitFor()
  })
  await check('上传文件进入真实检索链路', async () => {
    const response = await context.request.post('http://127.0.0.1:5189/api/v1/knowledge/search', { data: { query: '消融实验', top_k: 5 } })
    assert.ok(response.ok())
    const result = await response.json()
    assert.ok(JSON.stringify(result).includes(documentId))
    assert.ok(JSON.stringify(result).includes('91.7%'))
  })
  await check('刷新及切换会话恢复文件卡片且不串会话', async () => {
    await page.reload()
    await page.locator('.biz-card').filter({ hasText: '拖拽消融验收.md' }).waitFor()
    await page.locator('.session-item').filter({ hasText: '验收会话 B' }).click()
    await page.getByText('验收会话 B的原始提问', { exact: true }).waitFor()
    assert.equal(await page.locator('.biz-card').filter({ hasText: '拖拽消融验收.md' }).count(), 0)
    await page.locator('.session-item').filter({ hasText: '验收会话 A' }).click()
    await page.locator('.biz-card').filter({ hasText: '拖拽消融验收.md' }).waitFor()
  })
  await check('选择多个文件分别入库并展示结果', async () => {
    await page.locator('input[type=file]').setInputFiles([
      { name: '选择验收一.txt', mimeType: 'text/plain', buffer: Buffer.from('第一个上传资料') },
      { name: '选择验收二.md', mimeType: 'text/plain', buffer: Buffer.from('第二个上传资料') },
    ])
    await page.waitForFunction(() => document.querySelectorAll('.uploads .success').length === 2)
    await page.locator('.biz-card').filter({ hasText: '选择验收二.md' }).waitFor()
  })
  await check('不支持的文件类型明确提示且不发送上传请求', async () => {
    let requests = 0
    const count = request => { if (request.url().includes('/docs/upload')) requests++ }
    page.on('request', count)
    await drop([{ name: '不可上传.exe', body: 'data' }])
    await page.getByText('仅支持 PDF、TXT、Markdown', { exact: true }).waitFor()
    page.off('request', count)
    assert.equal(requests, 0)
  })
  await check('无可检索文字的文件显示失败且不生成成功卡片', async () => {
    await drop([{ name: '空白验收.txt', body: '  \n  ' }])
    await page.locator('.uploads .error').waitFor()
    assert.match(await page.locator('.uploads .error').innerText(), /未提取到可检索文字/)
    assert.equal(await page.locator('.biz-card').filter({ hasText: '空白验收.txt' }).count(), 0)
  })
  assert.deepEqual(errors, [])
  await page.screenshot({ path: resolve(output, 'upload.png'), fullPage: true })
} finally {
  writeFileSync(resolve(output, 'results.json'), JSON.stringify({ results, pageErrors: errors, embedding: 'mock; actual API/database/indexing and browser UI', realEmailSent: false }, null, 2))
  await browser.close()
}
