import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'

const source = readFileSync(new URL('../src/utils/chatPresentation.ts', import.meta.url), 'utf8')
const module = { exports: {} }
vm.runInNewContext(ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText, { exports: module.exports })
const { mergeReportCards, displayChatText } = module.exports
const id = '4744bd33-7d4c-4e0a-8445-e943e5de5e3e'
const rows = [
  { role: 'user', content: '生成周报' },
  { role: 'card', card: { type: 'report', id, status: 'draft' } },
  { role: 'assistant', content: '请提供邮箱' },
  { role: 'card', card: { type: 'report', id, status: 'draft' } },
  { role: 'card', card: { type: 'report', id, status: 'edited' } },
  { role: 'card', card: { type: 'report', id: 'another-report' } },
]
const merged = mergeReportCards(rows)
assert.equal(merged.length, 4)
assert.equal(merged[1].card.status, 'edited')
assert.equal(merged[2].content, '请提供邮箱')
assert.equal(displayChatText(`[active_report_id=${id}] 生成周报`), '生成周报')
assert.equal(displayChatText(`周报已生成（report_id=${id}），内容如下：`), '周报已生成，内容如下：')
assert.equal(displayChatText('准确率 78.3%，截止 2026-10-10'), '准确率 78.3%，截止 2026-10-10')

const component = readFileSync(new URL('../src/components/ReportCard.vue', import.meta.url), 'utf8')
const script = component.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
  .replace(/^import .*$/gm, '')
const edits = []
const events = []
let conflict = false
const sandbox = {
  storageKey: suffix => `labpilot_alice_${suffix}`,
  ref: (value) => ({ value }), watch: () => {},
  onBeforeUnmount: () => {}, onBeforeRouteLeave: () => {},
  window: { addEventListener: () => {}, removeEventListener: () => {} },
  localStorage: (() => {
    const store = new Map()
    return { getItem: k => store.get(k) || null, setItem: (k,v) => store.set(k,v), removeItem: k => store.delete(k) }
  })(),
  defineProps: () => ({ reportId: id }),
  defineEmits: () => (...args) => events.push(args),
  api: {
    getReport: async () => ({ body_markdown: '数据库完整正文', content_hash: 'latest-hash' }),
    editReport: async (...args) => {
      edits.push(args)
      if (conflict) throw new Error('409: version_conflict')
      return { ok: true, content_hash: 'saved-hash' }
    },
  },
}
const compiled = ts.transpileModule(script + '\nglobalThis.cardTest = { open, save, cancel, draft, body, hash, editing, error, persistDraft };', {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText
vm.runInNewContext(compiled, sandbox)
const c = sandbox.cardTest
await c.open(true)
assert.equal(c.draft.value, '数据库完整正文')
c.draft.value = '修改后完整正文'
await c.save()
assert.deepEqual(edits[0], [id, '修改后完整正文', 'latest-hash'])
assert.equal(c.body.value, '修改后完整正文')
assert.equal(c.editing.value, false)
assert.equal(events.find(e => e[0] === 'saved')[0], 'saved')
await c.open(true)
c.draft.value = '保留我的未保存修改'
conflict = true
await c.save()
assert.equal(c.editing.value, true)
assert.equal(c.draft.value, '保留我的未保存修改')
assert.match(c.error.value, /未覆盖/)
c.persistDraft()
const restoredSandbox = { ...sandbox }
vm.runInNewContext(compiled, restoredSandbox)
assert.equal(restoredSandbox.cardTest.draft.value, '保留我的未保存修改')
assert.equal(restoredSandbox.cardTest.hash.value, 'latest-hash')
assert.equal(restoredSandbox.cardTest.editing.value, true)
const otherUserSandbox = { ...sandbox, storageKey: suffix => `labpilot_bob_${suffix}` }
vm.runInNewContext(compiled, otherUserSandbox)
assert.equal(otherUserSandbox.cardTest.draft.value, '')
c.cancel()
assert.equal(c.draft.value, '数据库完整正文')
assert.equal(c.editing.value, false)
let unmount
let completeSave
const lateEvents = []
const lateSandbox = {
  ...sandbox,
  defineProps: () => ({ reportId: id, threadId: 'thread-A' }),
  onBeforeUnmount: callback => { unmount = callback },
  defineEmits: () => (...args) => lateEvents.push(args),
  api: {
    ...sandbox.api,
    editReport: () => new Promise(resolve => { completeSave = resolve }),
  },
}
vm.runInNewContext(compiled, lateSandbox)
const late = lateSandbox.cardTest
await late.open(true)
late.draft.value = '保存过程中离开'
const pendingSave = late.save()
unmount()
completeSave({ ok: true, content_hash: 'saved' })
await pendingSave
assert.equal(lateEvents.filter(e => e[0] === 'saved').length, 0)
console.log('Report card checks passed: duplicate merge, metadata display, full-body load, versioned save, conflict retention, cancel.')
