<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api/client'
import LoadingBar from '../components/LoadingBar.vue'
import EmptyState from '../components/EmptyState.vue'
import ReportCard from '../components/ReportCard.vue'

const report = ref<{
  id: string
  period_start: string
  period_end: string
  body_markdown: string
  source_refs: Record<string, unknown>
} | null>(null)
const loading = ref(false)
const editorBusy = ref(false)
const error = ref('')
const copied = ref(false)
const REPORT_KEY = 'labpilot_last_report'

const refProgress = computed(
  () => (report.value?.source_refs?.progress_entry_ids as string[] | undefined)?.length ?? 0,
)
const refTodos = computed(
  () => (report.value?.source_refs?.todo_event_ids as string[] | undefined)?.length ?? 0,
)

onMounted(async () => {
  const id = localStorage.getItem(REPORT_KEY)
  if (!id) return
  loading.value = true
  try {
    report.value = await api.getReport(id)
  } catch {
    error.value = '上次周报加载失败，请重试或重新生成。'
  } finally {
    loading.value = false
  }
})

async function generate() {
  if (editorBusy.value) return
  loading.value = true
  error.value = ''
  copied.value = false
  try {
    report.value = await api.weeklyReport()
    localStorage.setItem(REPORT_KEY, report.value.id)
  } catch (e) {
    error.value = String(e)
  } finally {
    loading.value = false
  }
}

async function copyReport() {
  if (!report.value) return
  try {
    const latest = await api.getReport(report.value.id)
    report.value.body_markdown = latest.body_markdown
    await navigator.clipboard.writeText(latest.body_markdown)
    copied.value = true
    setTimeout(() => (copied.value = false), 2000)
  } catch {
    error.value = '复制失败，请手动选择文本复制'
  }
}
</script>

<template>
  <div>
    <LoadingBar :active="loading" />
    <div v-if="error" class="alert error">{{ error }}</div>

    <div class="card">
      <h3>生成本周周报草稿</h3>
      <p class="muted-text" style="margin-top: -6px">
        只聚合已有进展与任务事件；无记录内容显示【待补充】，不自动编造。
      </p>
      <div class="row">
        <button @click="generate" :disabled="loading || editorBusy">
          {{ loading ? '生成中…' : report ? '重新生成' : '生成草稿' }}
        </button>
        <button v-if="report" class="secondary" @click="copyReport">
          {{ copied ? '已复制' : '复制 Markdown' }}
        </button>
      </div>
    </div>

    <div class="card" v-if="report">
      <h3>
        草稿 {{ report.period_start }} ~ {{ report.period_end }}
      </h3>
      <ReportCard
        :key="report.id"
        :report-id="report.id"
        :disabled="loading"
        @busy="editorBusy = $event"
        @saved="report.body_markdown = $event.body"
      />
    </div>

    <div class="card" v-if="report">
      <h3>来源核对</h3>
      <p class="muted-text" style="margin-top: -6px">
        本草稿只汇总下列记录，点击跳转对应页面核对原文；记录数与生成时一致。
      </p>
      <div class="meta ref-row">
        <RouterLink class="ref-link" to="/progress">进展记录（{{ refProgress }} 条）→</RouterLink>
        <RouterLink class="ref-link" to="/todos">任务动态（{{ refTodos }} 条）→</RouterLink>
        <RouterLink class="ref-link" to="/knowledge">资料库检索出处 →</RouterLink>
      </div>
    </div>

    <div class="card" v-else-if="!loading">
      <EmptyState text="还没有草稿。点击「生成草稿」，系统会汇总本周进展与任务事件。" />
    </div>
  </div>
</template>

<style scoped>
.ref-row {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}

.ref-link {
  color: var(--brand-ink);
  text-decoration: none;
  font-size: 13.5px;
  font-weight: 500;
  padding: 6px 12px;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.7);
  transition: border-color 0.15s ease, background 0.15s ease;
}

.ref-link:hover {
  border-color: var(--brand-accent);
  background: var(--brand-soft);
}

.doc {
  padding: 18px 20px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.8);
  margin-bottom: 14px;
}

.doc :deep(h1),
.doc :deep(h2),
.doc :deep(h3) {
  margin: 18px 0 8px;
  font-size: 16px;
  font-weight: 600;
}

.doc :deep(h1:first-child),
.doc :deep(h2:first-child),
.doc :deep(h3:first-child) {
  margin-top: 0;
}

.doc :deep(p) {
  margin: 0 0 10px;
  line-height: 1.75;
  font-size: 14px;
}

.doc :deep(ul),
.doc :deep(ol) {
  margin: 0 0 10px;
  padding-left: 22px;
}

.doc :deep(li) {
  margin: 4px 0;
  line-height: 1.7;
  font-size: 14px;
}

.doc :deep(strong) {
  font-weight: 600;
}

.doc :deep(code) {
  padding: 1px 6px;
  border-radius: 5px;
  background: rgba(28, 37, 54, 0.07);
  font-family: var(--font-mono);
  font-size: 13px;
}

.doc :deep(blockquote) {
  margin: 0 0 10px;
  padding: 6px 14px;
  border-left: 3px solid var(--brand-accent);
  border-radius: 0 8px 8px 0;
  background: rgba(91, 141, 239, 0.06);
  color: var(--ink-soft);
}

.doc :deep(.md-table-wrap) {
  overflow-x: auto;
  margin: 0 0 12px;
}

.doc :deep(table) {
  border-collapse: collapse;
  font-size: 13.5px;
}

.doc :deep(th),
.doc :deep(td) {
  border: 1px solid var(--line-strong);
  padding: 7px 12px;
  text-align: left;
}

.doc :deep(th) {
  background: rgba(47, 95, 208, 0.07);
  font-weight: 600;
}
</style>
