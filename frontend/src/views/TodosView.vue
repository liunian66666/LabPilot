<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { api, type Todo } from '../api/client'
import StatCard from '../components/StatCard.vue'
import EmptyState from '../components/EmptyState.vue'
import LoadingBar from '../components/LoadingBar.vue'

const views = ref<Record<string, Array<Record<string, string>>>>({
  today: [],
  this_week: [],
  overdue: [],
  waiting: [],
})
const all = ref<Todo[]>([])
const title = ref('')
const priority = ref('normal')
const source = ref('personal')
const dueRaw = ref('')
const loading = ref(false)
const error = ref('')
const msg = ref('')

/** 展开的编辑面板（todo id → 编辑表单状态）。 */
const editing = reactive<Record<string, { title: string; due_raw: string; priority: string; remind_at: string }>>({})

const priorityLabels: Record<string, string> = {
  urgent_important: '重要且紧急',
  important: '重要不紧急',
  urgent: '紧急不重要',
  normal: '普通',
}

const sourceLabels: Record<string, string> = {
  meeting: '组会',
  advisor: '导师',
  paper_ddl: '论文 DDL',
  personal: '个人',
  comm: '沟通',
}

const statusLabels: Record<string, string> = {
  todo: '待办',
  doing: '进行中',
  waiting: '等待',
  blocked: '受阻',
  done: '已完成',
  cancelled: '已取消',
}

function fmtRemind(v: string | null): string {
  if (!v) return ''
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    const [v, list] = await Promise.all([api.todoViews(), api.todos()])
    views.value = v
    all.value = list
  } catch (e) {
    error.value = String(e)
  } finally {
    loading.value = false
  }
}

async function createTodo() {
  if (!title.value.trim()) return
  error.value = ''
  try {
    // due_raw 由后端统一解析（含「前/截止」视为截止日，否则计划日），
    // 不会再绕过本表单选择的优先级与来源。
    await api.createTodo({
      title: title.value.trim(),
      due_raw: dueRaw.value.trim(),
      priority: priority.value as Todo['priority'],
      source: source.value as Todo['source'],
    })
    msg.value = '待办已创建'
    title.value = ''
    dueRaw.value = ''
    await refresh()
  } catch (e) {
    error.value = String(e)
  }
}

async function patch(id: string, payload: Record<string, unknown>, okMsg: string) {
  error.value = ''
  try {
    await api.updateTodo(id, payload)
    msg.value = okMsg
    delete editing[id]
    await refresh()
  } catch (e) {
    error.value = String(e)
  }
}

const complete = (id: string) => patch(id, { status: 'done', note: '手动完成' }, '已完成')
const cancel = (id: string) => patch(id, { status: 'cancelled', note: '手动取消' }, '已取消')

function toggleEdit(t: Todo) {
  if (editing[t.id]) {
    delete editing[t.id]
    return
  }
  editing[t.id] = {
    title: t.title,
    due_raw: '',
    priority: t.priority,
    remind_at: fmtRemind(t.remind_at),
  }
}

async function saveEdit(t: Todo) {
  const f = editing[t.id]
  if (!f) return
  const payload: Record<string, unknown> = { title: f.title.trim(), priority: f.priority }
  if (f.due_raw.trim()) payload.due_raw = f.due_raw.trim()
  // datetime-local 为空且原值存在 → 显式清除；有值 → 覆盖
  payload.remind_at = f.remind_at || null
  await patch(t.id, payload, '已保存修改')
}

async function markConfirmed(id: string) {
  await patch(id, { confirm_state: 'confirmed' }, '已标记确认')
}

const sections = [
  { key: 'overdue', label: '逾期', tone: 'danger' as const },
  { key: 'today', label: '今日', tone: 'brand' as const },
  { key: 'this_week', label: '本周', tone: 'brand' as const },
  { key: 'waiting', label: '等确认', tone: 'warn' as const },
]

onMounted(refresh)
</script>

<style scoped>
.list-item.done,
.list-item.cancelled {
  opacity: 0.62;
}

.list-item.done strong {
  text-decoration: line-through;
  text-decoration-color: rgba(28, 37, 54, 0.35);
}

.ops {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.ops .secondary {
  min-height: 30px;
  padding: 5px 10px;
  font-size: 12.5px;
}

.edit-panel {
  margin-top: 10px;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: rgba(247, 249, 253, 0.8);
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.edit-panel .field {
  margin: 0;
}

.edit-panel .field label {
  font-size: 11.5px;
  color: var(--muted);
}

.edit-panel .full {
  grid-column: 1 / -1;
}

.remind-set {
  color: var(--brand-ink);
  font-weight: 500;
}

@media (max-width: 720px) {
  .edit-panel {
    grid-template-columns: 1fr;
  }
}
</style>

<template>
  <div>
    <LoadingBar :active="loading" />
    <div v-if="error" class="alert error">{{ error }}</div>
    <div v-if="msg" class="alert success">{{ msg }}</div>

    <div class="card">
      <h3>新建待办</h3>
      <div class="grid-2">
        <div class="field">
          <label>事项</label>
          <input v-model="title" placeholder="例如：补完消融实验" />
        </div>
        <div class="field">
          <label>截止/日期（可选，如「下周五前」）</label>
          <input v-model="dueRaw" placeholder="下周五前 / 今天 / 2026-10-01" />
        </div>
        <div class="field">
          <label>优先级</label>
          <select v-model="priority">
            <option value="urgent_important">重要且紧急</option>
            <option value="important">重要不紧急</option>
            <option value="urgent">紧急不重要</option>
            <option value="normal">普通</option>
          </select>
        </div>
        <div class="field">
          <label>来源</label>
          <select v-model="source">
            <option value="meeting">组会</option>
            <option value="advisor">导师</option>
            <option value="paper_ddl">论文 DDL</option>
            <option value="personal">个人</option>
            <option value="comm">沟通</option>
          </select>
        </div>
      </div>
      <div class="row">
        <button @click="createTodo" :disabled="loading || !title.trim()">创建待办</button>
        <button class="secondary" @click="refresh">刷新</button>
      </div>
    </div>

    <div class="grid-4">
      <StatCard
        v-for="s in sections"
        :key="s.key"
        :label="s.label"
        :value="views[s.key]?.length ?? 0"
        :tone="s.tone"
      />
    </div>

    <div class="grid-2" style="margin-top: 16px">
      <div class="card" v-for="s in sections" :key="s.key">
        <h3>
          {{ s.label }}
          <span class="tag" :class="s.tone === 'danger' ? 'danger' : s.tone === 'warn' ? 'warn' : ''">
            {{ views[s.key]?.length ?? 0 }}
          </span>
        </h3>
        <EmptyState v-if="!views[s.key]?.length" text="暂无事项" />
        <div class="list">
          <div class="list-item" v-for="t in views[s.key] || []" :key="t.id">
            <div class="row" style="justify-content: space-between">
              <strong>{{ t.title }}</strong>
              <div class="ops">
                <button class="secondary" @click="toggleEdit(t as never as Todo)">编辑</button>
                <button class="secondary" @click="complete(t.id)">完成</button>
              </div>
            </div>
            <div class="meta">
              <span class="tag">{{ priorityLabels[t.priority] || t.priority }}</span>
              <span class="tag">{{ sourceLabels[t.source] || t.source }}</span>
              <span v-if="t.planned_date" class="tag ok">计划 {{ t.planned_date }}</span>
              <span v-if="t.due_at" class="tag warn">截止 {{ t.due_at }}</span>
            </div>
            <!-- 编辑面板（视图分区与全部待办共用） -->
            <div class="edit-panel" v-if="editing[t.id]">
              <label class="field full">
                <span>标题</span>
                <input v-model="editing[t.id].title" />
              </label>
              <label class="field">
                <span>改期（如「下周五前」，留空不变）</span>
                <input v-model="editing[t.id].due_raw" placeholder="留空 = 不改日期" />
              </label>
              <label class="field">
                <span>优先级</span>
                <select v-model="editing[t.id].priority">
                  <option value="urgent_important">重要且紧急</option>
                  <option value="important">重要不紧急</option>
                  <option value="urgent">紧急不重要</option>
                  <option value="normal">普通</option>
                </select>
              </label>
              <label class="field full">
                <span>定时邮件提醒（到点自动发，留空 = 清除）</span>
                <input v-model="editing[t.id].remind_at" type="datetime-local" />
              </label>
              <div class="row full" style="margin: 0">
                <button @click="saveEdit(t as never as Todo)">保存</button>
                <button class="secondary" @click="toggleEdit(t as never as Todo)">收起</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="card">
      <h3>全部待办（{{ all.length }}）</h3>
      <EmptyState v-if="!all.length" text="还没有任何待办，从上面新建一条开始" />
      <div class="list">
        <div
          class="list-item"
          v-for="t in all"
          :key="t.id"
          :class="{ done: t.status === 'done', cancelled: t.status === 'cancelled' }"
        >
          <div class="row" style="justify-content: space-between">
            <div style="min-width: 0">
              <strong>{{ t.title }}</strong>
              <div class="meta">
                <span class="tag" :class="t.status === 'done' ? 'ok' : t.status === 'cancelled' ? '' : ''">
                  {{ statusLabels[t.status] || t.status }}
                </span>
                <span v-if="t.confirm_state === 'needs_confirm'" class="tag warn">待确认</span>
                <span v-if="t.priority" class="tag">{{ priorityLabels[t.priority] || t.priority }}</span>
                <span v-if="t.planned_date" class="tag ok">计划 {{ t.planned_date }}</span>
                <span v-if="t.due_at" class="tag warn">截止 {{ t.due_at.slice(0, 10) }}</span>
                <span v-if="t.remind_at" class="tag remind-set">
                  提醒 {{ new Date(t.remind_at).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) }}
                </span>
              </div>
              <div v-if="t.source_quote" class="quote">{{ t.source_quote }}</div>
            </div>
            <div class="ops">
              <template v-if="t.status !== 'done' && t.status !== 'cancelled'">
                <button class="secondary" @click="toggleEdit(t)">编辑</button>
                <button v-if="t.confirm_state === 'needs_confirm'" class="secondary" @click="markConfirmed(t.id)">
                  标记确认
                </button>
                <button class="secondary" @click="complete(t.id)">完成</button>
                <button class="secondary" @click="cancel(t.id)">取消</button>
              </template>
              <button v-else-if="t.status === 'cancelled'" class="secondary" @click="patch(t.id, { status: 'todo', note: '重新打开' }, '已重新打开')">
                重新打开
              </button>
            </div>
          </div>
          <!-- 编辑面板 -->
          <div class="edit-panel" v-if="editing[t.id]">
            <label class="field full">
              <span>标题</span>
              <input v-model="editing[t.id].title" />
            </label>
            <label class="field">
              <span>改期（如「下周五前」，留空不变）</span>
              <input v-model="editing[t.id].due_raw" placeholder="留空 = 不改日期" />
            </label>
            <label class="field">
              <span>优先级</span>
              <select v-model="editing[t.id].priority">
                <option value="urgent_important">重要且紧急</option>
                <option value="important">重要不紧急</option>
                <option value="urgent">紧急不重要</option>
                <option value="normal">普通</option>
              </select>
            </label>
            <label class="field full">
              <span>定时邮件提醒（到点自动发，清空 = 取消提醒）</span>
              <input v-model="editing[t.id].remind_at" type="datetime-local" />
            </label>
            <div class="row full" style="margin: 0">
              <button @click="saveEdit(t)">保存</button>
              <button class="secondary" @click="toggleEdit(t)">收起</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
