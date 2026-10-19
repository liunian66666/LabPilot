<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, type DocumentDetail, type DocumentInfo, type KnowledgeResult } from '../api/client'
import LoadingBar from '../components/LoadingBar.vue'
import EmptyState from '../components/EmptyState.vue'

const file = ref<File | null>(null)
const query = ref('消融实验什么时候完成')
const result = ref<KnowledgeResult | null>(null)
const searched = ref(false)
const loading = ref(false)
const error = ref('')
const msg = ref('')

const docs = ref<DocumentInfo[]>([])
const docTypeLabels: Record<string, string> = {
  meeting_note: '组会纪要',
  paper: '论文',
  experiment_log: '实验记录',
  other: '其他',
}

/** 展开原文回看的文档 id。 */
const openDocId = ref('')
const openDoc = ref<DocumentDetail | null>(null)
const openLoading = ref(false)

/** 行内改名编辑态：正在编辑的文档 id + 输入值。 */
const editingId = ref('')
const editTitle = ref('')
const renaming = ref(false)

const docTitleById = computed(() => {
  const m: Record<string, string> = {}
  for (const d of docs.value) m[d.id] = d.title || d.filename
  return m
})

function docName(id: string): string {
  return docTitleById.value[id] || `文档 ${id.slice(0, 8)}`
}

function startRename(d: DocumentInfo) {
  editingId.value = d.id
  editTitle.value = d.title || d.filename
  error.value = ''
  msg.value = ''
}

function cancelRename() {
  editingId.value = ''
  editTitle.value = ''
}

async function saveRename() {
  const id = editingId.value
  const title = editTitle.value.trim()
  if (!id || !title) return
  renaming.value = true
  error.value = ''
  msg.value = ''
  try {
    await api.renameDoc(id, title)
    msg.value = `已重命名为「${title}」`
    cancelRename()
    await loadDocs()
  } catch (e) {
    error.value = String(e)
  } finally {
    renaming.value = false
  }
}

async function loadDocs() {
  try {
    docs.value = await api.docsList()
  } catch (e) {
    error.value = String(e)
  }
}

function onFile(e: Event) {
  const el = e.target as HTMLInputElement
  file.value = el.files?.[0] || null
  msg.value = ''
}

async function upload() {
  if (!file.value) return
  loading.value = true
  error.value = ''
  try {
    const res = await api.uploadDoc(file.value)
    msg.value = `已入库「${file.value.name}」 chunks=${res.chunks}`
    file.value = null
    const el = document.querySelector<HTMLInputElement>('#doc-input')
    if (el) el.value = ''
    await loadDocs()
  } catch (e) {
    error.value = String(e)
  } finally {
    loading.value = false
  }
}

async function removeDoc(d: DocumentInfo) {
  if (!window.confirm(`确认删除「${d.title || d.filename}」及其 ${d.chunks} 个检索分块？不可恢复。`)) return
  error.value = ''
  msg.value = ''
  try {
    await api.deleteDoc(d.id)
    msg.value = `已删除「${d.title || d.filename}」`
    if (openDocId.value === d.id) {
      openDocId.value = ''
      openDoc.value = null
    }
    await loadDocs()
  } catch (e) {
    error.value = String(e)
  }
}

async function toggleDoc(d: DocumentInfo) {
  if (openDocId.value === d.id) {
    openDocId.value = ''
    openDoc.value = null
    return
  }
  openDocId.value = d.id
  openDoc.value = null
  openLoading.value = true
  try {
    openDoc.value = await api.docDetail(d.id)
  } catch (e) {
    error.value = String(e)
  } finally {
    openLoading.value = false
  }
}

async function search() {
  loading.value = true
  error.value = ''
  result.value = null
  searched.value = true
  try {
    result.value = await api.knowledgeSearch(query.value)
  } catch (e) {
    error.value = String(e)
  } finally {
    loading.value = false
  }
}

onMounted(loadDocs)
</script>

<template>
  <div>
    <LoadingBar :active="loading || openLoading || renaming" />
    <div v-if="error" class="alert error">{{ error }}</div>
    <div v-if="msg" class="alert success">{{ msg }}</div>

    <div class="grid-2">
      <!-- 上传 -->
      <div class="card">
        <h3>上传课题资料</h3>
        <label class="dropzone" for="doc-input">
          <svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true">
            <path
              d="M12 16V4M7 9l5-5 5 5M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
          <span v-if="file" class="dz-file">{{ file.name }}</span>
          <span v-else class="dz-text">点击选择 PDF / TXT / MD 文件</span>
        </label>
        <input id="doc-input" type="file" accept=".pdf,.txt,.md" hidden @change="onFile" />
        <div class="row" style="margin-top: 12px">
          <button @click="upload" :disabled="!file || loading">入库</button>
        </div>
      </div>

      <!-- 检索 -->
      <div class="card">
        <h3>检索（带出处）</h3>
        <div class="field">
          <label>问题</label>
          <div class="search-row">
            <input v-model="query" placeholder="验证集准确率是多少" @keyup.enter="search" />
            <button @click="search" :disabled="loading || !query.trim()">检索</button>
          </div>
        </div>
        <p class="hint">命中不足时会明确返回 no_result，不会强行拼凑答案；引用可点「原文」回看出处。</p>
      </div>
    </div>

    <!-- 检索结果（紧跟检索卡片，位于资料列表上方） -->
    <div class="card" v-if="result">
      <h3>
        结果
        <span class="tag" :class="result.status === 'ok' ? 'ok' : 'danger'">
          {{ result.status === 'ok' ? '命中' : '无结果' }}
        </span>
      </h3>
      <p class="muted-text" style="margin-top: -6px">{{ result.answer_hint }}</p>
      <div v-if="result.citations?.length" class="list">
        <div class="list-item" v-for="(c, i) in result.citations" :key="c.chunk_id">
          <div class="meta" style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap">
            <span class="cite-no">{{ i + 1 }}</span>
            <span class="tag brand-tag">来源：{{ docName(c.document_id) }}</span>
            <span class="tag">{{ c.page_or_section }}</span>
            <span class="tag">相关度 {{ (c.score * 100).toFixed(0) }}%</span>
            <button class="secondary cite-btn" @click="toggleDoc({ id: c.document_id } as never)">
              原文
            </button>
          </div>
          <div class="quote">{{ c.quote }}</div>
        </div>
      </div>
    </div>
    <div class="card" v-else-if="searched && !loading">
      <EmptyState text="没有找到相关内容。可换个问法，或先上传相关资料。" />
    </div>

    <!-- 文档列表 -->
    <div class="card">
      <h3>资料列表（{{ docs.length }}）</h3>
      <EmptyState v-if="!docs.length" text="还没有入库资料。上传 PDF / TXT / MD 后可检索与回看。" />
      <div class="list">
        <div class="doc-item" v-for="d in docs" :key="d.id">
          <div class="row" style="justify-content: space-between; align-items: center">
            <!-- 编辑态：标题变成输入框 + 保存/取消 -->
            <div v-if="editingId === d.id" class="rename-edit">
              <input
                v-model="editTitle"
                class="rename-input"
                placeholder="文档标题"
                @keyup.enter="saveRename"
                @keyup.esc="cancelRename"
              />
              <div class="row" style="gap: 6px">
                <button class="secondary" @click="saveRename" :disabled="renaming || !editTitle.trim()">保存</button>
                <button class="secondary" @click="cancelRename">取消</button>
              </div>
            </div>
            <button v-else class="doc-toggle" @click="toggleDoc(d)" :aria-expanded="openDocId === d.id">
              <strong>{{ d.title || d.filename }}</strong>
              <span class="meta-inline">
                {{ docTypeLabels[d.doc_type] || d.doc_type }} · {{ d.chunks }} 块 ·
                {{ d.created_at.slice(0, 10) }}
              </span>
            </button>
            <div class="row" style="gap: 6px">
              <button class="secondary" @click="startRename(d)">改名</button>
              <button class="secondary danger-btn" @click="removeDoc(d)">删除</button>
            </div>
          </div>
          <div v-if="openDocId === d.id" class="doc-plain">
            <template v-if="openDoc && openDoc.id === d.id">
              <div class="chunk" v-for="c in openDoc.chunks" :key="c.seq">
                <span class="tag">{{ c.page_or_section || `块 ${c.seq}` }}</span>
                <p>{{ c.content }}</p>
              </div>
            </template>
            <p v-else class="hint" style="margin: 8px 0 0">原文加载中…</p>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dropzone {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 30px 16px;
  border: 1.5px dashed var(--line-strong);
  border-radius: 12px;
  background: rgba(247, 249, 253, 0.7);
  color: var(--muted);
  cursor: pointer;
  transition: border-color 0.15s ease, background 0.15s ease;
}

.dropzone:hover {
  border-color: var(--brand-accent);
  background: var(--brand-soft);
  color: var(--brand-ink);
}

.dropzone:focus-visible {
  outline: 2px solid var(--brand-accent);
  outline-offset: 2px;
}

.dz-text {
  font-size: 13px;
}

.dz-file {
  font-size: 13px;
  font-weight: 500;
  color: var(--brand-ink);
  max-width: 90%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.search-row {
  display: flex;
  gap: 10px;
}

.search-row input {
  flex: 1;
}

.hint {
  margin: 0;
  font-size: 12.5px;
  color: var(--muted);
  line-height: 1.6;
}

.cite-no {
  flex: none;
  width: 20px;
  height: 20px;
  display: grid;
  place-items: center;
  border-radius: 6px;
  background: var(--brand-soft);
  color: var(--brand-ink);
  font-size: 11.5px;
  font-weight: 600;
}

.doc-item {
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 11px 14px;
  background: rgba(255, 255, 255, 0.75);
}

.doc-toggle {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 3px;
  background: none;
  border: none;
  padding: 0;
  cursor: pointer;
  text-align: left;
  color: inherit;
}

.doc-toggle:hover strong {
  color: var(--brand-ink);
}

.meta-inline {
  font-size: 12px;
  color: var(--muted);
}

.danger-btn {
  color: #b42318;
}

.danger-btn:hover {
  background: rgba(180, 35, 24, 0.08);
}

.doc-plain {
  margin-top: 10px;
  padding: 10px 12px;
  border-top: 1px dashed var(--line);
  max-height: 320px;
  overflow-y: auto;
}

.chunk {
  padding: 8px 0;
}

.chunk + .chunk {
  border-top: 1px solid var(--line);
}

.chunk p {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--ink-soft);
  white-space: pre-wrap;
}

.brand-tag {
  background: rgba(47, 95, 208, 0.08);
  color: var(--brand-ink);
}

.cite-btn {
  min-height: 26px;
  padding: 2px 10px;
  font-size: 12px;
}

.rename-edit {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1;
}

.rename-input {
  width: 100%;
}
</style>
