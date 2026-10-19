<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { api } from '../api/client'

const props = defineProps<{ threadId: string; disabled?: boolean }>()
const emit = defineEmits<{
  busy: [value: boolean]
  uploaded: [value: { threadId: string; id: string; name: string }]
}>()
const input = ref<HTMLInputElement | null>(null)
const busy = ref(false)
const dragging = ref(false)
const rows = ref<Array<{ name: string; status: 'uploading' | 'success' | 'error'; message: string }>>([])
let alive = true
let depth = 0
onBeforeUnmount(() => { alive = false })
onBeforeRouteLeave(() => !busy.value)

function enter(event: DragEvent) {
  if (!event.dataTransfer?.types.includes('Files')) return
  event.preventDefault()
  depth++
  dragging.value = true
}
function leave() {
  depth = Math.max(0, depth - 1)
  if (!depth) dragging.value = false
}
function over(event: DragEvent) {
  if (event.dataTransfer?.types.includes('Files')) {
    event.preventDefault()
    event.dataTransfer.dropEffect = busy.value || props.disabled ? 'none' : 'copy'
  }
}
async function drop(event: DragEvent) {
  if (!event.dataTransfer?.files.length) return
  event.preventDefault()
  depth = 0
  dragging.value = false
  await upload(Array.from(event.dataTransfer.files))
}
async function selected(event: Event) {
  const target = event.target as HTMLInputElement
  const files = Array.from(target.files || [])
  target.value = ''
  await upload(files)
}

async function upload(files: File[]) {
  if (busy.value || props.disabled || !files.length) return
  const thread = props.threadId
  const valid: Array<{ file: File; index: number }> = []
  rows.value = files.map(file => {
    let message = ''
    if (!/\.(pdf|txt|md)$/i.test(file.name)) message = '仅支持 PDF、TXT、Markdown'
    else if (!file.size) message = '文件为空'
    else if (file.size > 20 * 1024 * 1024) message = '单个文件不能超过 20MB'
    return { name: file.name, status: message ? 'error' : 'uploading', message: message || '正在上传并同步知识库…' }
  })
  files.forEach((file, index) => {
    if (rows.value[index].status === 'uploading') valid.push({ file, index })
  })
  if (!valid.length) return
  busy.value = true
  emit('busy', true)
  try {
    for (const { file, index } of valid) {
      try {
        const result = await api.uploadDoc(file, 'other', thread)
        if (!alive) return
        rows.value[index] = { name: file.name, status: 'success', message: '已同步知识库' }
        emit('uploaded', { threadId: thread, id: result.document_id, name: file.name })
      } catch (error) {
        if (!alive) return
        rows.value[index] = { name: file.name, status: 'error', message: String(error).replace(/^Error: /, '') }
      }
    }
  } finally {
    busy.value = false
    if (alive) emit('busy', false)
  }
}
</script>

<template>
  <div class="chat-upload" :class="{ dragging }" @dragenter="enter" @dragleave="leave" @dragover="over" @drop="drop">
    <div v-if="dragging" class="drop-hint" role="status">{{ busy || disabled ? '请等待当前操作完成' : '松开文件，即可上传并同步知识库' }}</div>
    <slot />
    <div class="upload-actions">
      <button class="secondary attach-btn" :disabled="busy || disabled" @click="input?.click()">添加文件</button>
      <span>拖入 PDF / TXT / MD 即可入库 · 单个文件最多 20MB</span>
      <input ref="input" type="file" accept=".pdf,.txt,.md" multiple hidden aria-label="上传资料文件" @change="selected" />
    </div>
    <ul v-if="rows.length" class="uploads" aria-live="polite">
      <li v-for="(row, index) in rows" :key="index" :class="row.status"><strong>{{ row.name }}</strong><span>{{ row.message }}</span></li>
    </ul>
  </div>
</template>

<style scoped>
.chat-upload { position: relative; max-width: 780px; margin: 0 auto; border-radius: 26px; }
.dragging { outline: 2px dashed var(--brand-accent); outline-offset: 5px; }
.drop-hint { position: absolute; inset: 0; z-index: 2; display: grid; place-items: center; border-radius: 26px; background: #edf3fff2; color: var(--brand-ink); pointer-events: none; }
.upload-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin-top: 8px; color: var(--muted); font-size: 12px; }
.attach-btn { min-height: 30px; padding: 5px 12px; font-size: 12px; }
.uploads { list-style: none; padding: 0; margin: 8px 0; font-size: 12px; }
.uploads li { display: flex; flex-wrap: wrap; gap: 10px; padding: 4px 0; overflow-wrap: anywhere; }
.success span { color: #26734d; }
.error span { color: var(--danger); }
</style>
