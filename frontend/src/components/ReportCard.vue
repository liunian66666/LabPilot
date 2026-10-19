<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { api } from '../api/client'
import { renderMarkdown } from '../utils/markdown'
import { storageKey } from '../utils/auth'

const props = defineProps<{ reportId: string; contentHash?: string; disabled?: boolean; threadId?: string }>()
const emit = defineEmits<{
  saved: [value: { id: string; body: string; hash: string; threadId?: string }]
  busy: [value: boolean]
}>()
const expanded = ref(false)
const editing = ref(false)
const busy = ref(false)
const body = ref('')
const draft = ref('')
const hash = ref('')
const error = ref('')
const notice = ref('')
const draftKey = storageKey(`report_draft:${props.threadId || 'reports'}:${props.reportId}`)
let alive = true

function persistDraft() {
  if (!editing.value) return
  try {
    localStorage.setItem(draftKey, JSON.stringify({ draft: draft.value, hash: hash.value, body: body.value }))
  } catch {
    error.value = '草稿暂存失败，请复制保留内容后再离开。'
  }
}

function clearDraft() {
  localStorage.removeItem(draftKey)
}

try {
  const cached = JSON.parse(localStorage.getItem(draftKey) || 'null')
  if (cached && typeof cached.draft === 'string' && cached.hash) {
    draft.value = cached.draft
    body.value = cached.body || ''
    hash.value = cached.hash
    editing.value = expanded.value = true
    notice.value = '已恢复未保存修改。'
  }
} catch { /* invalid cache is ignored */ }

function beforeUnload(event: BeforeUnloadEvent) {
  persistDraft()
  if (busy.value || (editing.value && draft.value !== body.value)) {
    event.preventDefault()
    event.returnValue = ''
  }
}
window.addEventListener('beforeunload', beforeUnload)
onBeforeRouteLeave(() => {
  persistDraft()
  return !busy.value
})
onBeforeUnmount(() => {
  persistDraft()
  alive = false
  window.removeEventListener('beforeunload', beforeUnload)
})

function setBusy(value: boolean) {
  busy.value = value
  if (alive) emit('busy', value)
}

watch(() => props.contentHash, () => {
  // An agent edit invalidates the preview; retain any unsaved user draft.
  if (!editing.value && !busy.value) expanded.value = false
})

async function open(edit = false) {
  setBusy(true)
  error.value = ''
  notice.value = ''
  try {
    const report = await api.getReport(props.reportId)
    if (!alive) return
    body.value = report.body_markdown
    hash.value = report.content_hash
    draft.value = body.value
    expanded.value = true
    editing.value = edit
    persistDraft()
  } catch {
    error.value = '周报加载失败，请重试。'
  } finally {
    setBusy(false)
  }
}

async function save() {
  if (!draft.value.trim() || !hash.value) return
  persistDraft()
  setBusy(true)
  error.value = ''
  notice.value = ''
  try {
    const result = await api.editReport(props.reportId, draft.value, hash.value)
    if (!result.ok) throw new Error('save_failed')
    clearDraft()
    if (!alive) return
    body.value = draft.value
    hash.value = result.content_hash
    editing.value = false
    emit('saved', { id: props.reportId, body: body.value, hash: hash.value, threadId: props.threadId })
    notice.value = '周报已保存。已有邮件草稿不会自动更新，请重新拟信后确认发送。'
  } catch (e) {
    error.value = String(e).includes('409')
      ? '周报已有其他修改，未覆盖。请先复制保留当前内容，再取消编辑并重新打开最新版本。'
      : '保存失败，修改内容已保留，请重试。'
  } finally {
    setBusy(false)
  }
}

function cancel() {
  clearDraft()
  draft.value = body.value
  editing.value = false
  error.value = ''
}
</script>

<template>
  <section class="report-card" aria-label="周报草稿">
    <header><strong>周报草稿</strong><span>可查看全文并修改</span></header>
    <div v-if="error" class="alert error" role="alert">{{ error }}</div>
    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <template v-if="editing">
      <label :for="`report-editor-${reportId}`">编辑周报内容（Markdown）</label>
      <textarea :id="`report-editor-${reportId}`" v-model="draft" rows="14" :disabled="busy" @input="persistDraft" />
      <p class="notice">未保存修改会暂存在此浏览器，返回后可继续编辑。</p>
      <div class="actions">
        <button :disabled="busy || disabled || !draft.trim()" @click="save">{{ busy ? '保存中…' : '保存修改' }}</button>
        <button class="secondary" :disabled="busy" @click="cancel">取消</button>
      </div>
    </template>
    <template v-else>
      <div v-if="expanded" class="report-body" v-html="renderMarkdown(body)"></div>
      <div class="actions">
        <button class="secondary" :disabled="busy || disabled" @click="expanded ? expanded = false : open()">
          {{ busy ? '加载中…' : expanded ? '收起全文' : '查看全文' }}
        </button>
        <button :disabled="busy || disabled" @click="open(true)">编辑周报</button>
      </div>
    </template>
  </section>
</template>

<style scoped>
.report-card { padding: 16px; border: 1px solid var(--line); border-radius: 13px; background: #fff; }
header { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-bottom: 12px; }
header span, .notice { color: var(--muted); font-size: 13px; }
.actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }
textarea { width: 100%; box-sizing: border-box; resize: vertical; margin-top: 8px; line-height: 1.7; }
.report-body { overflow-wrap: anywhere; line-height: 1.7; }
.report-body :deep(pre) { overflow: auto; }
.report-body :deep(table) { display: block; overflow: auto; }
.report-body :deep(h1), .report-body :deep(h2), .report-body :deep(h3) { font-size: 17px; }
</style>
