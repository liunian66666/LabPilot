<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { api, type Todo } from '../api/client'
import LoadingBar from '../components/LoadingBar.vue'
import EmptyState from '../components/EmptyState.vue'

const enabled = ref(false)
const toEmail = ref('')
const hoursBefore = ref(24)
const msg = ref('')
const error = ref('')
const due = ref<Array<Record<string, string>>>([])
const todos = ref<Todo[]>([])
const loading = ref(false)

/** 展开编辑的待办 id。 */
const editingId = ref('')
/** 待编辑的提醒时刻（datetime-local 格式）。 */
const editValue = reactive<Record<string, string>>({})

/** 只展示仍活跃、且与时间相关的待办。 */
const activeTodos = computed(() =>
  todos.value.filter((t) => t.status !== 'done' && t.status !== 'cancelled'),
)

function fmtLocal(v: string | null): string {
  if (!v) return ''
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function fmtShow(v: string | null): string {
  if (!v) return ''
  return new Date(v).toLocaleString('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const s = await fetch('/api/v1/reminders/settings').then((r) => r.json())
    enabled.value = !!s.enabled
    toEmail.value = s.to_email || ''
    hoursBefore.value = s.hours_before || 24
    const d = await fetch('/api/v1/reminders/due').then((r) => r.json())
    due.value = d.items || []
    todos.value = await api.todos()
  } catch (e) {
    error.value = String(e)
  } finally {
    loading.value = false
  }
}

async function save() {
  error.value = ''
  msg.value = ''
  try {
    const res = await fetch('/api/v1/reminders/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        enabled: enabled.value,
        to_email: toEmail.value,
        hours_before: hoursBefore.value,
      }),
    })
    const data = await res.json()
    if (!res.ok) throw new Error(JSON.stringify(data))
    msg.value = enabled.value
      ? `已开启：截止前 ${hoursBefore.value} 小时自动提醒到 ${toEmail.value}（授权后无需逐次确认）`
      : '已关闭自动提醒'
    await load()
  } catch (e) {
    error.value = String(e)
  }
}

async function runNow() {
  msg.value = ''
  error.value = ''
  try {
    const res = await fetch('/api/v1/reminders/run', { method: 'POST' })
    const data = await res.json()
    msg.value = `调度结果：${JSON.stringify(data)}`
    await load()
  } catch (e) {
    error.value = String(e)
  }
}

function startEdit(t: Todo) {
  editingId.value = editingId.value === t.id ? '' : t.id
  editValue[t.id] = fmtLocal(t.remind_at)
}

async function saveRemind(t: Todo) {
  error.value = ''
  try {
    // datetime-local 有值 → 设置/覆盖；清空 → 显式 null 清除
    const iso = editValue[t.id] ? new Date(editValue[t.id]).toISOString() : null
    await api.updateTodo(t.id, { remind_at: editValue[t.id] || null })
    msg.value = iso ? `已设置「${t.title}」的定时提醒：${fmtShow(iso)}` : `已清除「${t.title}」的定时提醒`
    editingId.value = ''
    await load()
  } catch (e) {
    error.value = String(e)
  }
}

function clearRemind(t: Todo) {
  editValue[t.id] = ''
  saveRemind(t)
}

onMounted(load)
</script>

<template>
  <div>
    <LoadingBar :active="loading" />
    <div v-if="error" class="alert error">{{ error }}</div>
    <div v-if="msg" class="alert success">{{ msg }}</div>

    <div class="card">
      <h3>截止自动提醒（全局）</h3>
      <p class="sub">
        开启后系统将在截止前自动向该邮箱发送提醒（一次性授权，无需每次确认）。
        给老师等他人发信仍需逐次审批。
      </p>
      <div class="field">
        <label>
          <input v-model="enabled" type="checkbox" style="width: auto; margin-right: 8px" />
          开启自动提醒
        </label>
      </div>
      <div class="grid-2">
        <div class="field">
          <label>提醒邮箱</label>
          <input v-model="toEmail" placeholder="me@example.com" />
        </div>
        <div class="field">
          <label>提前小时数</label>
          <input v-model.number="hoursBefore" type="number" min="1" max="336" />
        </div>
      </div>
      <button @click="save">保存设置</button>
      <button class="secondary" style="margin-left: 8px" @click="runNow">立即检查一次</button>
    </div>

    <div class="card">
      <h3>单任务定时提醒</h3>
      <p class="sub">
        为某条待办指定精确的提醒时刻（如「明天 9:30」），到点自动发邮件到上方授权邮箱；
        与全局「截止前 N 小时」提醒互不影响，可同时存在。对话里说「明天9点提醒我做X」也会写入这里。
      </p>
      <EmptyState
        v-if="!activeTodos.length"
        text="暂无活跃待办。先在「四维待办」或对话中创建任务。"
      />
      <div class="list">
        <div class="list-item" v-for="t in activeTodos" :key="t.id">
          <div class="row" style="justify-content: space-between; align-items: center">
            <div style="min-width: 0">
              <strong>{{ t.title }}</strong>
              <div class="meta">
                <span v-if="t.due_at" class="tag warn">截止 {{ t.due_at.slice(0, 10) }}</span>
                <span v-else-if="t.planned_date" class="tag">计划 {{ t.planned_date }}</span>
                <span v-if="t.remind_at" class="tag set">已设定时提醒 {{ fmtShow(t.remind_at) }}</span>
                <span v-else class="tag">未设定时提醒</span>
              </div>
            </div>
            <button class="secondary" @click="startEdit(t)">
              {{ editingId === t.id ? '收起' : t.remind_at ? '修改' : '设置' }}
            </button>
          </div>
          <div v-if="editingId === t.id" class="remind-editor">
            <input v-model="editValue[t.id]" type="datetime-local" />
            <button @click="saveRemind(t)">保存提醒</button>
            <button v-if="t.remind_at" class="secondary" @click="clearRemind(t)">清除提醒</button>
          </div>
        </div>
      </div>
    </div>

    <div class="card">
      <h3>当前提醒窗口内待办（{{ due.length }}）</h3>
      <div class="list">
        <div v-if="!due.length" class="meta">暂无</div>
        <div class="list-item" v-for="t in due" :key="t.id">
          <strong>{{ t.title }}</strong>
          <div class="meta">
            <span class="tag">{{ t.priority }}</span>
            <span v-if="t.due_at" class="tag warn">{{ t.due_at }}</span>
            <span v-else-if="t.planned_date" class="tag">{{ t.planned_date }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sub {
  margin: -6px 0 12px;
  font-size: 12.5px;
  color: var(--muted);
  line-height: 1.6;
}

.tag.set {
  background: rgba(47, 95, 208, 0.1);
  color: var(--brand-ink);
}

.remind-editor {
  margin-top: 10px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
}

.remind-editor input {
  max-width: 240px;
}
</style>
