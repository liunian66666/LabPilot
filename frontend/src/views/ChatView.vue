<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import type { StreamEvent } from '../utils/chatStream'
import { api, type ApprovalInfo } from '../api/client'
import { getContext, newTaskContext, patchContext, switchThread } from '../api/context'
import { storageKey, getUsername } from '../utils/auth'
import LoadingBar from '../components/LoadingBar.vue'
import ReportCard from '../components/ReportCard.vue'
import ChatUpload from '../components/ChatUpload.vue'
import { displayChatText, mergeReportCards } from '../utils/chatPresentation'
import { renderMarkdown } from '../utils/markdown'

interface ChatMsg {
  role: 'user' | 'assistant' | 'card'
  content: string
  time: string
  card?: {
    type: 'report' | 'approval' | 'todo' | 'document'
    id: string
    title: string
    body?: string
    hash?: string
    version?: string
    status?: string
  }
}

interface SessionInfo {
  thread_id: string
  title: string
  updated_at: number
}

/** 当前用户的会话列表 key。 */
function sessionsKey(): string {
  return storageKey('sessions')
}

/** 当前用户某 thread 的消息缓存 key。 */
function chatMsgKey(thread: string): string {
  return storageKey(`chat_${thread}`)
}

const input = ref('')
const messages = ref<ChatMsg[]>([])
const sessions = ref<SessionInfo[]>([])
const loading = ref(false)
const error = ref('')
const historyError = ref('')
const threadId = ref('')
const listEl = ref<HTMLElement | null>(null)
const busyReports = ref<string[]>([])
const uploading = ref(false)
const streamStatus = ref('正在思考…')
let streamController: AbortController | undefined
const owner = getUsername()
let alive = true
onBeforeUnmount(() => { alive = false; streamController?.abort() })
let historyRequest = 0
let messageRevision = 0

function reportBusy(id: string, busy: boolean) {
  busyReports.value = busy
    ? [...new Set([...busyReports.value, id])]
    : busyReports.value.filter((value) => value !== id)
}

function documentUploaded(value: { threadId: string; id: string; name: string }) {
  if (value.threadId !== threadId.value) return
  push({ role: 'card', content: value.name, time: nowTime(), card: {
    type: 'document', id: value.id, title: value.name,
    body: '已同步知识库，可围绕这份资料提问。', status: 'indexed',
  } })
}

/* ---------- 侧栏收起 / 会话重命名 ---------- */
const RAIL_KEY = 'labpilot_rail_open'
const railOpen = ref(localStorage.getItem(RAIL_KEY) !== '0')
const renamingId = ref('')
const renameDraft = ref('')

function toggleRail() {
  railOpen.value = !railOpen.value
  localStorage.setItem(RAIL_KEY, railOpen.value ? '1' : '0')
}

function startRename(s: SessionInfo, e: Event) {
  e.stopPropagation()
  renamingId.value = s.thread_id
  renameDraft.value = s.title
  nextTick(() => {
    const el = document.querySelector<HTMLInputElement>('.rename-input')
    el?.focus()
    el?.select()
  })
}

async function commitRename() {
  if (!renamingId.value) return
  const id = renamingId.value
  const title = renameDraft.value.trim()
  renamingId.value = ''
  if (!title) return
  try {
    const saved = await api.renameThread(id, title)
    const found = sessions.value.find((s) => s.thread_id === id)
    if (found) found.title = saved.title
    saveSessionList()
  } catch {
    error.value = '会话重命名失败，请重试。'
  }
}

function cancelRename() {
  renamingId.value = ''
}

const suggestions = [
  '查一下消融实验相关的待办，结合本周进展写周报，最后准备一封给老师的邮件',
  '生成本周周报，然后发给老师',
  '我有哪些待办还没完成？',
]

/* ---------- 会话索引（左栏） ---------- */

// 已删除会话登记：防止 listThreads 在删除请求完成前返回旧数据，把会话"复活"
const deletedThreads = new Set<string>()

async function loadSessionList() {
  try {
    const raw = localStorage.getItem(sessionsKey())
    sessions.value = raw ? (JSON.parse(raw) as SessionInfo[]) : []
  } catch {
    sessions.value = []
  }
  sessions.value.sort((a, b) => b.updated_at - a.updated_at)
  // 以服务端会话表为准补全（清缓存后可恢复）
  await api
    .listThreads()
    .then((res) => {
      if (!alive || getUsername() !== owner) return
      const remote = (res.threads || [])
        .filter((t) => !deletedThreads.has(t.thread_id))
        .map((t) => ({
        thread_id: t.thread_id,
        title: t.title || t.thread_id.slice(0, 12),
        updated_at: t.updated_at ? Date.parse(t.updated_at) : Date.now(),
      }))
      const map = new Map(remote.map((s) => [s.thread_id, s]))
      for (const s of sessions.value) {
        if (!map.has(s.thread_id)) map.set(s.thread_id, s)
      }
      sessions.value = [...map.values()].sort((a, b) => b.updated_at - a.updated_at)
      saveSessionList()
    })
    .catch(() => {
      /* 本地列表兜底 */
    })
}

function saveSessionList() {
  localStorage.setItem(sessionsKey(), JSON.stringify(sessions.value))
}

function touchSession(thread: string, title?: string) {
  const found = sessions.value.find((s) => s.thread_id === thread)
  if (found) {
    if (title && (found.title === '新会话' || !found.title)) found.title = title
    found.updated_at = Date.now()
  } else {
    sessions.value.unshift({ thread_id: thread, title: title || '新会话', updated_at: Date.now() })
    void api.createThread(thread, title || '新会话').catch(() => undefined)
  }
  saveSessionList()
}

async function removeSession(id: string, e: Event) {
  e.stopPropagation()
  if (loading.value || uploading.value || busyReports.value.length) return
  const target = sessions.value.find((s) => s.thread_id === id)
  const name = target?.title || '新会话'
  if (!window.confirm(`确认删除会话「${name}」？本地与服务端的聊天记录将一并删除，不可恢复。`)) return
  deletedThreads.add(id)
  sessions.value = sessions.value.filter((s) => s.thread_id !== id)
  saveSessionList()
  localStorage.removeItem(chatMsgKey(id))
  try {
    await api.deleteThread(id)
  } catch {
    /* 删除失败也不阻塞本地清理 */
  }
  if (id === threadId.value) newSession()
}

function switchSession(id: string) {
  if (loading.value || uploading.value || busyReports.value.length || id === threadId.value) return
  // 按 thread 恢复 active_*，避免跨会话串对象
  const ctx = switchThread(id)
  threadId.value = ctx.thread_id
  loadMessages()
  restoreFromServer()
  scrollToBottom()
}

/* ---------- 消息存取 ---------- */

function msgKey() {
  return chatMsgKey(threadId.value)
}

function saveMessages() {
  try {
    localStorage.setItem(msgKey(), JSON.stringify(messages.value))
  } catch {
    /* ignore */
  }
}

function loadMessages() {
  try {
    const raw = localStorage.getItem(msgKey())
    messages.value = raw ? mergeReportCards(JSON.parse(raw) as ChatMsg[]) : []
    for (const message of messages.value) {
      if (message.card?.type === 'approval') message.card.status = 'checking'
    }
  } catch {
    messages.value = []
  }
}

function nowTime() {
  return new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

async function scrollToBottom() {
  await nextTick()
  listEl.value?.scrollTo({ top: listEl.value.scrollHeight, behavior: 'smooth' })
}

function push(msg: ChatMsg) {
  messageRevision++
  messages.value = mergeReportCards([...messages.value, msg])
  saveMessages()
  scrollToBottom()
}

function reportSaved(value: { id: string; body: string; hash: string; threadId?: string }) {
  if (value.threadId && value.threadId !== threadId.value) return
  const msg = messages.value.find((m) => m.card?.type === 'report' && m.card.id === value.id)
  if (msg?.card) {
    msg.card.hash = value.hash
    msg.card.body = value.body
  }
  patchContext({ active_report_id: value.id })
  saveMessages()
}

function approvalLabel(status?: string) {
  const labels: Record<string, string> = {
    pending: '待确认发送', approved: '已批准', sending: '发送中', sent: '已发送',
    rejected: '已拒绝', failed: '发送失败', unknown: '发送结果待核查', expired: '已过期',
    checking: '正在核对状态', deciding: '正在提交确认', unavailable: '状态未核实，请刷新',
  }
  return labels[status || ''] || '请查看邮件详情'
}

async function restoreFromServer() {
  const reqThread = threadId.value
  const request = ++historyRequest
  const revision = messageRevision
  historyError.value = ''
  try {
    const hist = await api.chatHistory(reqThread)
    // 快速切换会话：丢弃迟到响应
    if (!alive || getUsername() !== owner || reqThread !== threadId.value || request !== historyRequest || revision !== messageRevision) return
    const rows = (hist.messages || []).filter((m) => ['user', 'assistant', 'card'].includes(m.role))
    if (rows.length) {
      messages.value = mergeReportCards(rows.map((m) => {
        const card = m.card as Record<string, string> | undefined
        return {
          role: (m.role === 'card' ? 'card' : m.role) as ChatMsg['role'],
          content: m.content || '',
          time: m.created_at ? new Date(m.created_at).toLocaleString('zh-CN') : '',
          card: card
            ? {
                type: (card.type as 'report' | 'approval' | 'todo' | 'document') || 'report',
                id: card.id || '',
                title: card.title || '',
                status: card.status,
                hash: card.content_hash || '',
                version: card.content_hash || '',
                body: card.body || m.content,
              }
            : undefined,
        }
      }))
      saveMessages()
    }
    if (hist.pending_approval_id) {
      await loadApprovalCard(hist.pending_approval_id, undefined, undefined, undefined, reqThread)
    }
    for (const row of messages.value.filter((m) => m.card?.type === 'approval' && m.card.status === 'checking')) {
      if (row.card) await loadApprovalCard(row.card.id, undefined, undefined, undefined, reqThread)
    }
  } catch (e) {
    if (!alive || getUsername() !== owner || reqThread !== threadId.value) return
    historyError.value = '历史加载失败，可重试'
    console.warn('history load failed', e)
  }
}

async function loadApprovalCard(
  approvalId: string,
  title?: string,
  version?: string,
  status?: string,
  reqThread = threadId.value,
) {
  const revision = messageRevision
  try {
    let info: ApprovalInfo | null = null
    try {
      info = await api.getApproval(approvalId)
    } catch {
      info = null
    }
    if (!alive || getUsername() !== owner || reqThread !== threadId.value || revision !== messageRevision) return
    patchContext({ active_approval_id: approvalId })
    const card: ChatMsg = {
      role: 'card',
      content: '待审批邮件',
      time: '',
      card: {
        type: 'approval',
        id: approvalId,
        title: info?.payload?.subject || title || '邮件草稿',
        body: `${info?.payload?.to_email || ''}\n${info?.payload?.body || ''}`,
        version: info?.payload_version || version || '',
        status: info?.status || 'unavailable',
      },
    }
    const idx = messages.value.findIndex(
      (m) => m.card?.type === 'approval' && m.card.id === approvalId,
    )
    if (idx >= 0) {
      card.time = messages.value[idx].time
      messages.value[idx] = card
    } else {
      messages.value.push(card)
    }
    saveMessages()
    scrollToBottom()
  } catch {
    /* ignore */
  }
}

function parseIds(text: string) {
  const report = text.match(/report_id=([0-9a-f-]{8,})/i)
  const hash = text.match(/hash=([0-9a-f]{6,})/i)
  const todo = text.match(/todo(?:_id)?=([0-9a-f-]{8,})/i)
  return { reportId: report?.[1], hash: hash?.[1], todoId: todo?.[1] }
}

async function send() {
  const text = input.value.trim()
  if (!text || loading.value || uploading.value || busyReports.value.length) return
  error.value = ''
  input.value = ''
  resetInputHeight()
  const reqThread = threadId.value
  push({ role: 'user', content: text, time: nowTime() })
  touchSession(reqThread, text.slice(0, 24))

  loading.value = true
  streamStatus.value = '正在思考…'
  streamController = new AbortController()
  let streamedReply: ChatMsg | undefined
  try {
    const ctx = getContext()
    let res: StreamEvent['data']
    await api.chatStream({
      session_id: reqThread,
      messages: [{ role: 'user', content: text }],
      active_report_id: ctx.active_report_id,
      active_todo_id: ctx.active_todo_id,
      active_approval_id: ctx.active_approval_id,
    }, (event) => {
      if (!alive || getUsername() !== owner || reqThread !== threadId.value || streamController?.signal.aborted) return
      if (event.type === 'status') streamStatus.value = event.text || '正在处理…'
      if (event.type === 'reset' && streamedReply) streamedReply.content = ''
      if (event.type === 'delta' || event.type === 'done') {
        if (!streamedReply) {
          push({ role: 'assistant', content: '', time: nowTime() })
          streamedReply = messages.value[messages.value.length - 1]
        }
        if (event.type === 'delta') streamedReply.content += event.text || ''
        else if (event.data) {
          res = event.data
          streamedReply.content = event.data.reply
          saveMessages()
        }
        scrollToBottom()
      }
    }, streamController.signal)
    // 切换会话后丢弃迟到响应
    if (!alive || getUsername() !== owner || reqThread !== threadId.value) return
    if (!res) throw new Error('未收到完整回复，请重试。')

    if (res.artifacts?.length) {
      for (const a of res.artifacts) {
        if (a.type === 'report') {
          patchContext({ active_report_id: a.id })
          push({
            role: 'card',
            content: '周报',
            time: nowTime(),
            card: {
              type: 'report',
              id: a.id,
              title: a.title || '周报草稿',
              status: a.status,
              hash: a.content_hash || '',
            },
          })
        }
        if (a.type === 'todo') {
          patchContext({ active_todo_id: a.id })
        }
        if (a.type === 'approval') {
          await loadApprovalCard(a.id, a.title, a.content_hash, a.status, reqThread)
        }
      }
    }
    const pendingApproval = res.pending_approval_id
    if (pendingApproval) {
      const exists = messages.value.some(
        (m) => m.card?.type === 'approval' && m.card.id === pendingApproval,
      )
      if (!exists) await loadApprovalCard(pendingApproval, undefined, undefined, undefined, reqThread)
    }
    touchSession(threadId.value)
  } catch (e) {
    if (!streamController?.signal.aborted) {
      error.value = String(e)
      if (streamedReply) {
        streamedReply.content += '\n\n（回复中断，以上内容可能不完整。）'
        saveMessages()
      }
    }
  } finally {
    loading.value = false
    streamController = undefined
    scrollToBottom()
  }
}

async function decideCard(msg: ChatMsg, decision: 'approved' | 'rejected') {
  if (loading.value || !msg.card || msg.card.status !== 'pending') return
  const reqThread = threadId.value
  const approvalId = msg.card.id
  const version = msg.card.version || ''
  messageRevision++
  loading.value = true
  msg.card.status = 'deciding'
  saveMessages()
  try {
    const r = await api.decide({
      approval_id: approvalId,
      decision,
      payload_version: version,
      session_id: reqThread,
    })
    if (!alive || getUsername() !== owner || reqThread !== threadId.value) return
    for (const row of messages.value) {
      if (row.card?.type === 'approval' && row.card.id === approvalId) row.card.status = r.status
    }
    push({
      role: 'assistant',
      content:
        decision === 'rejected'
          ? '已拒绝，不会外发。'
          : `已批准。${approvalLabel(r.status)}。`,
      time: nowTime(),
    })
    saveMessages()
  } catch (e) {
    if (alive && getUsername() === owner && reqThread === threadId.value) error.value = String(e)
  } finally {
    if (alive && getUsername() === owner && reqThread === threadId.value) {
      await loadApprovalCard(approvalId, undefined, undefined, undefined, reqThread)
    }
    loading.value = false
  }
}

function onEnter(e: KeyboardEvent) {
  if (e.isComposing) return
  if (e.shiftKey) return // Shift+Enter 交还给 textarea 默认换行
  e.preventDefault()
  send()
}

/* 输入框随内容自动撑高（上限交给 CSS max-height），发送后复位单行 */
const inputEl = ref<HTMLTextAreaElement>()

function autoGrow() {
  const el = inputEl.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${el.scrollHeight}px`
}

function resetInputHeight() {
  void nextTick(() => {
    const el = inputEl.value
    if (!el) return
    el.style.height = 'auto'
    autoGrow()
  })
}

function newSession() {
  if (loading.value || uploading.value || busyReports.value.length) return
  threadId.value = newTaskContext().thread_id
  messages.value = []
  error.value = ''
  saveMessages()
  loadSessionList()
}

onMounted(async () => {
  threadId.value = getContext().thread_id
  await loadSessionList()
  if (!alive || getUsername() !== owner) return
  // 清缓存后先恢复服务端已有会话，避免登录时立即新建空会话。
  if (sessions.value.length && !sessions.value.some((session) => session.thread_id === threadId.value)) {
    threadId.value = switchThread(sessions.value[0].thread_id).thread_id
  }
  loadMessages()
  touchSession(threadId.value)
  await restoreFromServer()
  scrollToBottom()
})
</script>

<template>
  <div>
    <LoadingBar :active="loading" />
    <div v-if="error" class="alert error" style="margin: 12px 16px">{{ error }}</div>
    <div v-if="historyError" class="alert error" style="margin: 0 16px 8px">
      {{ historyError }}
      <button class="secondary" style="margin-left: 8px" @click="restoreFromServer">重试</button>
    </div>

    <div class="chat-page">
      <!-- 左侧会话列表（可收起） -->
      <aside class="rail" :class="{ collapsed: !railOpen }">
        <div class="rail-head">
          <button v-if="railOpen" class="new-chat" @click="newSession">
            <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
              <path
                d="M12 5v14M5 12h14"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
              />
            </svg>
            新会话
          </button>
          <button
            class="rail-toggle"
            :aria-label="railOpen ? '收起侧栏' : '展开侧栏'"
            :title="railOpen ? '收起侧栏' : '展开侧栏'"
            @click="toggleRail"
          >
            <svg
              viewBox="0 0 24 24"
              width="16"
              height="16"
              aria-hidden="true"
              :style="{ transform: railOpen ? 'none' : 'rotate(180deg)' }"
            >
              <path
                d="M15 6l-6 6 6 6"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
                fill="none"
              />
            </svg>
          </button>
        </div>
        <template v-if="railOpen">
          <p class="rail-label">会话记录</p>
          <div class="session-list">
            <button
              v-for="s in sessions"
              :key="s.thread_id"
              class="session-item"
              :class="{ active: s.thread_id === threadId }"
              @click="switchSession(s.thread_id)"
            >
              <input
                v-if="renamingId === s.thread_id"
                v-model="renameDraft"
                class="rename-input"
                aria-label="会话名称"
                @click.stop
                @keydown.enter.prevent="commitRename"
                @keydown.esc.prevent="cancelRename"
                @blur="commitRename"
              />
              <template v-else>
                <span class="session-title">{{ s.title }}</span>
                <span
                  class="session-act"
                  role="button"
                  aria-label="重命名会话"
                  title="重命名"
                  @click="startRename(s, $event)"
                >
                  <svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true">
                    <path
                      d="M4 20h4L19 9a2.1 2.1 0 0 0-3-3L5 17v3z"
                      stroke="currentColor"
                      stroke-width="1.8"
                      stroke-linejoin="round"
                      fill="none"
                    />
                  </svg>
                </span>
                <span
                  class="session-del"
                  role="button"
                  aria-label="删除会话"
                  title="删除"
                  @click="removeSession(s.thread_id, $event)"
                >×</span>
              </template>
            </button>
            <p v-if="!sessions.length" class="rail-empty">暂无历史会话</p>
          </div>
        </template>
      </aside>

      <!-- 右侧对话区 -->
      <section class="conversation">
        <div ref="listEl" class="msg-scroll">
          <div class="col">
            <div v-if="!messages.length" class="chat-empty">
              <p class="greeting">有什么可以帮你？</p>
              <p class="greeting-sub">安排任务、起草周报与邮件——留痕可溯，外发先确认。</p>
              <div class="chips">
                <button v-for="s in suggestions" :key="s" class="chip" @click="input = s; send()">
                  {{ s }}
                </button>
              </div>
            </div>

            <template v-for="(m, i) in messages" :key="`${threadId}:${m.card ? m.card.type + ':' + m.card.id : i}`">
              <!-- 业务卡片：周报 / 审批 -->
              <ReportCard
                v-if="m.role === 'card' && m.card?.type === 'report'"
                :report-id="m.card.id"
                :content-hash="m.card.hash"
                :thread-id="threadId"
                :disabled="loading"
                @busy="reportBusy(m.card.id, $event)"
                @saved="reportSaved"
              />
              <div v-else-if="m.role === 'card' && m.card" class="biz-card">
                <div class="biz-card-head">
                  <strong>{{ m.card.title }}</strong>
                  <span v-if="m.card.type === 'approval'" class="tag warn">{{ approvalLabel(m.card.status) }}</span>
                </div>
                <div v-if="m.card.body" class="biz-card-body md-body" v-html="renderMarkdown(m.card.body)"></div>
                <div v-if="m.card.type === 'approval'" class="row" style="margin-top: 10px">
                  <button
                    class="danger"
                    :disabled="loading || m.card.status !== 'pending'"
                    @click="decideCard(m, 'rejected')"
                  >
                    拒绝
                  </button>
                  <button
                    :disabled="loading || m.card.status !== 'pending'"
                    @click="decideCard(m, 'approved')"
                  >
                    批准并发送
                  </button>
                </div>
              </div>

              <!-- 用户消息：右侧浅灰气泡 -->
              <div v-else-if="m.role === 'user'" class="turn turn-user">
                <div class="user-bubble">{{ displayChatText(m.content) }}</div>
              </div>

              <!-- 助手消息：无气泡平铺正文（Markdown 渲染） -->
              <div v-else class="turn turn-ai">
                <div class="ai-body md-body" v-html="renderMarkdown(displayChatText(m.content))"></div>
                <div class="ai-time">{{ m.time }}</div>
              </div>
            </template>

            <div v-if="loading" class="turn turn-ai">
              <div class="thinking">
                <span class="dot"></span><span class="dot"></span><span class="dot"></span>
                <span class="thinking-label">{{ streamStatus }}</span>
              </div>
            </div>
          </div>
        </div>

        <!-- 底部悬浮胶囊输入框 -->
        <div class="composer-wrap">
          <ChatUpload :key="threadId" :thread-id="threadId" :disabled="loading || busyReports.length > 0" @busy="uploading = $event" @uploaded="documentUploaded">
          <div class="composer">
            <textarea
              ref="inputEl"
              v-model="input"
              rows="1"
              placeholder="询问任何问题…"
              @input="autoGrow"
              @keydown.enter="onEnter"
            ></textarea>
            <button
              class="send-btn"
              :disabled="loading || uploading || !input.trim()"
              aria-label="发送"
              @click="send"
            >
              <svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true">
                <path
                  d="M12 19V5M5 12l7-7 7 7"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2.2"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                />
              </svg>
            </button>
          </div>
          </ChatUpload>
          <p class="composer-hint">Enter 发送 · Shift+Enter 换行 · 给他人发信需先确认邮件内容</p>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.chat-page {
  display: flex;
  height: 100vh;
  overflow: hidden;
}

/* ---------- 左侧会话栏 ---------- */
.rail {
  width: 232px;
  flex: none;
  display: flex;
  flex-direction: column;
  padding: 14px 10px;
  background: rgba(243, 246, 251, 0.85);
  backdrop-filter: blur(8px);
  border-right: 1px solid var(--line);
  transition: width 0.18s ease, padding 0.18s ease;
}

.rail.collapsed {
  width: 52px;
  padding: 14px 8px;
}

.rail-head {
  display: flex;
  align-items: center;
  gap: 6px;
}

.rail-head .new-chat {
  flex: 1;
  min-width: 0;
}

.rail.collapsed .rail-head {
  justify-content: center;
}

.rail-toggle {
  flex: none;
  width: 32px;
  height: 32px;
  min-height: 32px;
  padding: 0;
  display: grid;
  place-items: center;
  border-radius: 8px;
  background: transparent;
  color: var(--muted);
  box-shadow: none;
}

.rail-toggle:hover:not(:disabled) {
  filter: none;
  transform: none;
  background: rgba(28, 37, 54, 0.06);
  color: var(--ink);
  box-shadow: none;
}

.rail-toggle svg {
  transition: transform 0.18s ease;
}

.new-chat {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-height: 40px;
  padding: 9px 14px;
  border-radius: 11px;
  font-size: 14px;
  font-weight: 400;
  background: transparent;
  color: var(--ink-soft);
  box-shadow: inset 0 0 0 1px var(--line);
}

.new-chat:hover:not(:disabled) {
  filter: none;
  transform: none;
  background: rgba(28, 37, 54, 0.045);
  color: var(--ink);
  box-shadow: inset 0 0 0 1px var(--line-strong);
}

.rail-label {
  margin: 16px 6px 6px;
  font-size: 11px;
  letter-spacing: 0.06em;
  color: var(--muted);
}

.session-list {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.session-item {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  min-height: 38px;
  padding: 8px 10px;
  border-radius: 9px;
  background: transparent;
  color: var(--ink-soft);
  box-shadow: none;
  text-align: left;
  font-size: 13px;
}

.session-item:hover:not(:disabled) {
  filter: none;
  transform: none;
  background: rgba(28, 37, 54, 0.05);
  box-shadow: none;
}

.session-item.active {
  background: var(--brand-soft);
  color: var(--brand-ink);
}

.session-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-del {
  flex: none;
  width: 18px;
  height: 18px;
  display: grid;
  place-items: center;
  border-radius: 5px;
  font-size: 14px;
  color: var(--muted);
  opacity: 0;
  transition: opacity 0.12s ease;
}

.session-act {
  flex: none;
  width: 18px;
  height: 18px;
  display: grid;
  place-items: center;
  border-radius: 5px;
  color: var(--muted);
  opacity: 0;
  transition: opacity 0.12s ease;
}

.session-item:hover .session-del,
.session-item:hover .session-act {
  opacity: 1;
}

.session-act:hover {
  background: rgba(47, 95, 208, 0.12);
  color: var(--brand-ink);
}

.rename-input {
  flex: 1;
  min-width: 0;
  min-height: 28px;
  padding: 3px 8px;
  border: 1px solid var(--brand-accent);
  border-radius: 7px;
  background: #fff;
  color: var(--ink);
  font-size: 13px;
  font-family: inherit;
}

.rename-input:focus {
  outline: none;
  box-shadow: 0 0 0 3px rgba(47, 95, 208, 0.15);
}

.session-del:hover {
  background: rgba(187, 58, 48, 0.12);
  color: var(--danger);
}

.rail-empty {
  margin: 8px 6px;
  font-size: 12px;
  color: var(--muted);
}

/* ---------- 右侧对话区 ---------- */
.conversation {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.msg-scroll {
  flex: 1;
  overflow-y: auto;
}

.col {
  max-width: 780px;
  margin: 0 auto;
  padding: 28px 20px 12px;
  display: flex;
  flex-direction: column;
  gap: 24px;
}

/* 空态 */
.chat-empty {
  margin: auto;
  text-align: center;
  padding-top: 14vh;
}

.greeting {
  margin: 0 0 6px;
  font-family: var(--font-display);
  font-size: 30px;
  font-weight: 700;
  letter-spacing: 0.01em;
}

.greeting-sub {
  margin: 0 0 24px;
  color: var(--muted);
  font-size: 14px;
}

.chips {
  display: flex;
  flex-direction: column;
  gap: 9px;
  align-items: center;
}

.chip {
  min-height: 40px;
  padding: 9px 18px;
  border-radius: 999px;
  font-size: 13px;
  background: rgba(255, 255, 255, 0.85);
  color: var(--ink-soft);
  box-shadow: inset 0 0 0 1px var(--line-strong);
}

.chip:hover:not(:disabled) {
  filter: none;
  transform: none;
  background: var(--brand-soft);
  color: var(--brand-ink);
  box-shadow: inset 0 0 0 1px rgba(47, 95, 208, 0.35);
}

/* 用户消息：右侧浅灰气泡 */
.turn {
  display: flex;
  flex-direction: column;
}

.turn-user {
  align-items: flex-end;
}

.user-bubble {
  max-width: 72%;
  padding: 10px 16px;
  border-radius: 18px;
  border-bottom-right-radius: 6px;
  background: rgba(28, 37, 54, 0.06);
  color: var(--ink);
  font-size: 14.5px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
}

/* 助手消息：无气泡平铺正文 */
.turn-ai {
  align-items: flex-start;
}

.ai-body {
  width: 100%;
  font-size: 15px;
  line-height: 1.72;
  color: var(--ink);
  word-break: break-word;
}

.ai-time {
  margin-top: 4px;
  font-size: 11px;
  color: var(--muted);
  opacity: 0.7;
}

/* 思考中 */
.thinking {
  display: flex;
  align-items: center;
  gap: 5px;
  color: var(--muted);
  font-size: 13px;
}

.thinking-label {
  margin-left: 4px;
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--brand-accent);
  animation: blink 1.2s ease-in-out infinite;
}

.dot:nth-child(2) {
  animation-delay: 0.2s;
}

.dot:nth-child(3) {
  animation-delay: 0.4s;
}

@keyframes blink {
  0%,
  80%,
  100% {
    opacity: 0.25;
  }
  40% {
    opacity: 1;
  }
}

@media (prefers-reduced-motion: reduce) {
  .dot {
    animation: none;
    opacity: 0.6;
  }

  .rail,
  .rail-toggle svg {
    transition: none;
  }
}

/* ---------- 业务卡片 ---------- */
.biz-card {
  padding: 13px 16px;
  border-radius: 13px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid var(--line);
  box-shadow: 0 4px 14px rgba(28, 36, 51, 0.06);
}

.biz-card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.biz-card-body {
  font-size: 13px;
  color: var(--ink);
  line-height: 1.55;
  max-height: 200px;
  overflow: auto;
  margin-bottom: 6px;
}

/* ---------- 底部输入区 ---------- */
.composer-wrap {
  flex: none;
  padding: 6px 20px 14px;
}

.composer {
  max-width: 780px;
  margin: 0 auto;
  display: flex;
  align-items: flex-end;
  gap: 10px;
  padding: 10px 10px 10px 18px;
  border: 1px solid var(--line-strong);
  border-radius: 26px;
  background: rgba(255, 255, 255, 0.95);
  box-shadow: var(--shadow-lift);
}

.composer textarea {
  flex: 1;
  min-height: 40px;
  max-height: 160px;
  padding: 8px 0;
  border: none;
  border-radius: 0;
  background: transparent;
  resize: none;
  overflow-y: auto;
  line-height: 1.55;
}

.composer textarea:focus {
  border: none;
  box-shadow: none;
}

.send-btn {
  flex: none;
  width: 38px;
  height: 38px;
  min-height: 38px;
  padding: 0;
  display: grid;
  place-items: center;
  border-radius: 50%;
}

.send-btn:disabled {
  background: #d7ddea;
  box-shadow: none;
}

.composer-hint {
  max-width: 780px;
  margin: 8px auto 0;
  text-align: center;
  font-size: 11.5px;
  color: var(--muted);
}

/* ---------- Markdown 渲染样式 ---------- */
.md-body :deep(p) {
  margin: 0 0 10px;
}

.md-body :deep(p:last-child) {
  margin-bottom: 0;
}

.md-body :deep(h3),
.md-body :deep(h4),
.md-body :deep(h5),
.md-body :deep(h6) {
  margin: 16px 0 8px;
  font-size: 15.5px;
  font-weight: 600;
}

.md-body :deep(h3:first-child) {
  margin-top: 0;
}

.md-body :deep(ul),
.md-body :deep(ol) {
  margin: 0 0 10px;
  padding-left: 22px;
}

.md-body :deep(li) {
  margin: 3px 0;
}

.md-body :deep(strong) {
  font-weight: 600;
}

.md-body :deep(code) {
  padding: 1px 6px;
  border-radius: 5px;
  background: rgba(28, 37, 54, 0.07);
  font-family: var(--font-mono);
  font-size: 13px;
}

.md-body :deep(pre) {
  margin: 0 0 10px;
  padding: 12px 14px;
  border-radius: 11px;
  background: #f6f8fc;
  border: 1px solid var(--line);
  overflow-x: auto;
}

.md-body :deep(pre code) {
  padding: 0;
  background: none;
}

.md-body :deep(blockquote) {
  margin: 0 0 10px;
  padding: 6px 14px;
  border-left: 3px solid var(--brand-accent);
  border-radius: 0 8px 8px 0;
  background: rgba(91, 141, 239, 0.06);
  color: var(--ink-soft);
}

.md-body :deep(.md-table-wrap) {
  overflow-x: auto;
  margin: 0 0 12px;
}

.md-body :deep(table) {
  border-collapse: collapse;
  font-size: 13.5px;
  min-width: 60%;
}

.md-body :deep(th),
.md-body :deep(td) {
  border: 1px solid var(--line-strong);
  padding: 7px 12px;
  text-align: left;
  line-height: 1.5;
}

.md-body :deep(th) {
  background: rgba(47, 95, 208, 0.07);
  font-weight: 600;
}

/* ---------- 移动端 ---------- */
@media (max-width: 960px) {
  .rail {
    display: none;
  }

  .user-bubble {
    max-width: 86%;
  }

  .col {
    padding: 20px 14px 8px;
  }

  .composer-wrap {
    padding: 6px 12px 10px;
  }
}
</style>
