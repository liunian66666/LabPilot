import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'

const src = fs.readFileSync(new URL('../src/views/ChatView.vue', import.meta.url), 'utf8')
  .match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
const presentation = fs.readFileSync(new URL('../src/utils/chatPresentation.ts', import.meta.url), 'utf8')
const exports = {}
vm.runInNewContext(ts.transpileModule(presentation, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, { exports })
const storage = new Map()
let resolveApproval
let resolveHistory
const patches = []
const renamed = []
const sandbox = {
  getUsername: () => 'dev', storageKey: suffix => `labpilot_dev_${suffix}`,
  ref: v => ({ value: v }), nextTick: async () => {}, onMounted: () => {}, onBeforeUnmount: () => {}, console,
  localStorage: { getItem: k => storage.get(k) || null, setItem: (k,v) => storage.set(k,v) },
  patchContext: p => patches.push(p), ...exports,
  api: {
    getApproval: () => new Promise(resolve => { resolveApproval = resolve }),
    chatHistory: () => new Promise(resolve => { resolveHistory = resolve }),
    renameThread: async (id,title) => { renamed.push([id,title]); return { title } },
  },
}
vm.runInNewContext(ts.transpileModule(src + '\nglobalThis.review={loadApprovalCard,restoreFromServer,threadId,messages,push,reportSaved,commitRename,renamingId,renameDraft,sessions};', {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText, sandbox)
const c = sandbox.review
c.threadId.value = 'A'
const approval = c.loadApprovalCard('approval-A')
c.threadId.value = 'B'
resolveApproval({ payload: { subject: 'A邮件', body: 'A正文' }, payload_version: 'v-A', status: 'pending' })
await approval
assert.equal(c.messages.value.length, 0)
assert.equal(patches.length, 0)
c.reportSaved({ id: 'report-A', threadId: 'A', body: '正文', hash: 'v' })
assert.equal(patches.length, 0)

const delayed = c.restoreFromServer()
c.push({ role: 'user', content: '刚发送的新消息', time: '12:00' })
resolveHistory({ messages: [{ role: 'user', content: '旧快照', created_at: '2026-01-01T08:00:00+08:00' }] })
await delayed
assert.equal(c.messages.value[0].content, '刚发送的新消息')
const restored = c.restoreFromServer()
const timestamp = '2026-01-01T08:00:00+08:00'
resolveHistory({ messages: [{ role: 'assistant', content: '历史消息', created_at: timestamp }] })
await restored
assert.equal(c.messages.value[0].time, new Date(timestamp).toLocaleString('zh-CN'))
c.sessions.value = [{ thread_id: 'B', title: '旧标题' }]
c.renamingId.value = 'B'
c.renameDraft.value = '消融实验汇报'
await c.commitRename()
assert.deepEqual(renamed, [['B', '消融实验汇报']])
assert.equal(c.sessions.value[0].title, '消融实验汇报')
console.log('Chat history checks passed: late approval isolation, late save isolation, stale history rejection, original timestamp, persisted rename.')
