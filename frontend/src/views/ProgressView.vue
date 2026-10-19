<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type ProgressEntry, type Todo } from '../api/client'
import LoadingBar from '../components/LoadingBar.vue'
import EmptyState from '../components/EmptyState.vue'

const workDone = ref('')
const observations = ref('')
const blockers = ref('')
const todoId = ref('')
const todos = ref<Todo[]>([])
const recent = ref<ProgressEntry[]>([])
const msg = ref('')
const error = ref('')
const saving = ref(false)

const openTodos = ref<Todo[]>([])

/* 时间范围：默认近 7 天；支持自定义起止日期 */
type RangeKey = '1d' | '3d' | '7d' | '1m' | 'custom'
const RANGES: Array<{ key: RangeKey; label: string; days: number }> = [
  { key: '1d', label: '一天', days: 1 },
  { key: '3d', label: '三天', days: 3 },
  { key: '7d', label: '七天', days: 7 },
  { key: '1m', label: '近一个月', days: 30 },
]
const rangeKey = ref<RangeKey>('7d')
const customStart = ref('')
const customEnd = ref('')
const rangeText = ref('')

function isoDaysAgo(n: number) {
  const d = new Date(Date.now() - (n - 1) * 86400000)
  return d.toISOString().slice(0, 10)
}

async function loadRecent() {
  try {
    const opts =
      rangeKey.value === 'custom'
        ? { start_date: customStart.value, end_date: customEnd.value || isoDaysAgo(1) }
        : { days: RANGES.find((r) => r.key === rangeKey.value)?.days ?? 7 }
    const res = await api.progressList(opts)
    recent.value = res.items
    rangeText.value = `${res.start_date} ~ ${res.end_date}`
  } catch (e) {
    error.value = String(e)
  }
}

function setRange(key: RangeKey) {
  rangeKey.value = key
  if (key !== 'custom') void loadRecent()
}

function applyCustom() {
  if (!customStart.value) return
  void loadRecent()
}

onMounted(async () => {
  try {
    todos.value = await api.todos()
    openTodos.value = todos.value.filter((t) => t.status !== 'done')
  } catch (e) {
    error.value = String(e)
  }
  void loadRecent()
})

async function save() {
  if (!workDone.value.trim() || saving.value) return
  saving.value = true
  error.value = ''
  try {
    const res = await api.progress({
      work_done: workDone.value,
      observations: observations.value,
      blockers: blockers.value,
      todo_id: todoId.value || undefined,
    })
    msg.value = `已记录进展 ${res.id.slice(0, 8)}，将计入本周周报`
    workDone.value = ''
    observations.value = ''
    blockers.value = ''
    todoId.value = ''
    void loadRecent()
  } catch (e) {
    error.value = String(e)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div>
    <LoadingBar :active="saving" />
    <div v-if="error" class="alert error">{{ error }}</div>
    <div v-if="msg" class="alert success">{{ msg }}</div>

    <div class="progress-grid">
      <!-- 左：表单 -->
      <div class="card">
        <h3>记一条进展</h3>
        <div class="field">
          <label>本次做了什么</label>
          <textarea v-model="workDone" rows="4" placeholder="整理消融脚本并跑通两组基线" />
        </div>
        <div class="grid-2">
          <div class="field">
            <label>观察 / 结果（可选）</label>
            <textarea v-model="observations" rows="4" placeholder="准确率约 78.3%，低于预期" />
          </div>
          <div class="field">
            <label>阻塞（可选）</label>
            <textarea v-model="blockers" rows="4" placeholder="缺一份数据说明" />
          </div>
        </div>
        <div class="field">
          <label>关联待办（可选）</label>
          <select v-model="todoId">
            <option value="">不关联</option>
            <option v-for="t in openTodos" :key="t.id" :value="t.id">{{ t.title }}</option>
          </select>
        </div>
        <div class="row">
          <button @click="save" :disabled="saving || !workDone.trim()">
            {{ saving ? '保存中…' : '保存进展' }}
          </button>
        </div>
      </div>

      <!-- 右：说明 -->
      <div class="card side-card">
        <h3>为什么记进展？</h3>
        <ul class="why-list">
          <li>周报只聚合已记录的事实——没记的内容会以【待补充】出现，不会被编造。</li>
          <li>每条进展可关联一个待办，周报里能自动对应到任务来源。</li>
          <li>写清「观察 / 结果」和「阻塞」，周报的问题与阻塞小节才有内容。</li>
        </ul>
        <div class="side-stat">
          <span class="side-stat-num">{{ openTodos.length }}</span>
          <span class="side-stat-label">个进行中的待办可关联</span>
        </div>
      </div>
    </div>

    <!-- 最近进展（含对话中由 Agent 记录的条目） -->
    <div class="card recent-card">
      <div class="recent-bar">
        <h3>最近进展</h3>
        <div class="bar-right">
          <div class="range-picker">
            <button
              v-for="r in RANGES"
              :key="r.key"
              class="range-btn"
              :class="{ active: rangeKey === r.key }"
              @click="setRange(r.key)"
            >
              {{ r.label }}
            </button>
            <button
              class="range-btn"
              :class="{ active: rangeKey === 'custom' }"
              @click="setRange('custom')"
            >
              自定义
            </button>
          </div>
          <!-- 自定义日期行：与「一天」左对齐，起止+查询同一行 -->
          <div v-if="rangeKey === 'custom'" class="custom-row">
            <input v-model="customStart" type="date" aria-label="开始日期" />
            <span class="custom-sep">至</span>
            <input v-model="customEnd" type="date" aria-label="结束日期" />
            <button class="custom-apply" :disabled="!customStart" @click="applyCustom">查询</button>
          </div>
        </div>
      </div>
      <p v-if="rangeText" class="range-hint">范围：{{ rangeText }} · 共 {{ recent.length }} 条</p>
      <EmptyState v-if="!recent.length" text="该时间范围内没有进展记录——在上方表单或对话里说「记一条进展」即可" />
      <div v-else class="recent-list">
        <div v-for="p in recent" :key="p.id" class="recent-item">
          <div class="recent-head">
            <span class="recent-date">{{ p.work_date }}</span>
            <span v-if="p.todo_title" class="recent-todo">关联：{{ p.todo_title }}</span>
          </div>
          <div class="recent-done">{{ p.work_done }}</div>
          <div v-if="p.observations" class="recent-extra">观察：{{ p.observations }}</div>
          <div v-if="p.blockers" class="recent-extra recent-block">阻塞：{{ p.blockers }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.recent-card {
  margin-top: 18px;
}

.recent-bar {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  flex-wrap: wrap;
}

.bar-right {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
}

.range-picker {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.range-btn {
  padding: 5px 12px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: transparent;
  font-size: 12.5px;
  color: var(--ink-soft);
  cursor: pointer;
}

.range-btn.active {
  background: var(--brand-soft);
  border-color: var(--brand-accent);
  color: var(--brand-accent);
}

/* 覆盖全局 input{width:100%}：日期框紧凑单行 */
.custom-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.custom-row input {
  width: 128px;
  flex: none;
  padding: 4px 8px;
  border: 1px solid var(--line);
  border-radius: 8px;
  font-size: 12.5px;
  min-height: 0;
}

.custom-sep {
  font-size: 12.5px;
  color: var(--ink-soft);
}

.custom-apply {
  padding: 4px 12px;
  border: none;
  border-radius: 8px;
  background: var(--brand-accent);
  color: #fff;
  font-size: 12.5px;
  cursor: pointer;
}

.custom-apply:disabled {
  background: #d7ddea;
  cursor: not-allowed;
}

.range-hint {
  margin: 0 0 10px;
  font-size: 12.5px;
  color: var(--ink-soft);
}

.recent-list {
  display: grid;
  gap: 12px;
}

.recent-item {
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--surface-soft, #f7f9fc);
}

.recent-head {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 6px;
}

.recent-date {
  font-size: 12.5px;
  color: var(--ink-soft);
  font-variant-numeric: tabular-nums;
}

.recent-todo {
  font-size: 12.5px;
  color: var(--brand-accent);
}

.recent-done {
  font-size: 14px;
  color: var(--ink);
  line-height: 1.6;
}

.recent-extra {
  margin-top: 4px;
  font-size: 13px;
  color: var(--ink-soft);
  line-height: 1.6;
}

.recent-block {
  color: #b0563a;
}

.progress-grid {
  display: grid;
  grid-template-columns: minmax(0, 7fr) minmax(0, 4fr);
  gap: 18px;
  align-items: start;
}

.why-list {
  margin: 0;
  padding-left: 18px;
  color: var(--ink-soft);
  font-size: 13.5px;
  line-height: 1.8;
}

.why-list li::marker {
  color: var(--brand-accent);
}

.side-stat {
  margin-top: 18px;
  padding: 14px 16px;
  border-radius: 12px;
  background: var(--brand-soft);
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.side-stat-num {
  font-family: var(--font-display);
  font-size: 26px;
  font-weight: 700;
  color: var(--brand-ink);
}

.side-stat-label {
  font-size: 13px;
  color: var(--brand-ink);
}

@media (max-width: 1100px) {
  .progress-grid {
    grid-template-columns: 1fr;
  }
}
</style>
