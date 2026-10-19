/** LabPilot API 客端（经 Vite 代理到后端 :8000）。 */
import { readChatStream, type StreamEvent } from '../utils/chatStream'
import { authHeaders, handleUnauthorized } from '../utils/auth'

export type Priority = 'urgent_important' | 'important' | 'urgent' | 'normal'
export type TodoStatus = 'todo' | 'doing' | 'waiting' | 'blocked' | 'done' | 'cancelled'
export type TodoSource = 'meeting' | 'advisor' | 'paper_ddl' | 'personal' | 'comm'

export interface Todo {
  id: string
  title: string
  detail: string
  due_at: string | null
  planned_date: string | null
  remind_at: string | null
  priority: Priority
  source: TodoSource
  status: TodoStatus
  confirm_state: string
  source_quote: string | null
  created_at: string
  updated_at: string
}

export interface ExtractedTask {
  title: string
  detail: string
  due_raw: string
  planned_date: string | null
  needs_confirm: boolean
  priority: Priority
  source: TodoSource
  source_quote: string
  deliverable: string
  todo_id?: string | null
}

export interface ProgressEntry {
  id: string
  work_date: string
  work_done: string
  observations: string
  blockers: string
  todo_id: string
  todo_title: string
  source_quote: string
}

export interface DocumentInfo {
  id: string
  title: string
  filename: string
  doc_type: string
  created_at: string
  chunks: number
}

export interface DocumentDetail extends Omit<DocumentInfo, 'chunks'> {
  chunks: Array<{ seq: number; page_or_section: string; content: string }>
}

export interface Citation {
  document_id: string
  chunk_id: string
  quote: string
  page_or_section: string
  score: number
}

export interface KnowledgeResult {
  status: 'ok' | 'no_result'
  answer_hint: string
  citations: Citation[]
}

export interface ZoteroConnection {
  connected: boolean
  zotero_user_id?: string
}

export interface ZoteroItem {
  zotero_key: string
  title: string
  authors?: string[] | string
  year?: string | number
  venue?: string
  doi?: string
  abstract?: string
  url?: string
  tags?: string[]
}

export interface ApprovalInfo {
  approval_id?: string
  id?: string
  status: string
  payload?: Record<string, string>
  payload_version: string
  exec_result?: string | null
}

const BASE = '/api/v1'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      ...authHeaders(),
      ...(init?.headers || {}),
    },
    ...init,
  })
  if (res.status === 401) handleUnauthorized()
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail || JSON.stringify(body)
    } catch {
      /* ignore */
    }
    throw new Error(`${res.status}: ${detail}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  register: (username: string, password: string) =>
    request<{ access_token: string; user_id: string; username: string }>(
      '/auth/register',
      { method: 'POST', body: JSON.stringify({ username, password }) },
    ),
  login: (username: string, password: string) =>
    request<{ access_token: string; user_id: string; username: string }>(
      '/auth/login',
      { method: 'POST', body: JSON.stringify({ username, password }) },
    ),
  chatStream: async (
    payload: { session_id: string; messages: Array<{ role: string; content: string }>; active_report_id?: string; active_todo_id?: string; active_approval_id?: string },
    receive: (event: StreamEvent) => void,
    signal?: AbortSignal,
  ) => readChatStream(await fetch(`${BASE}/chat/stream`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json', Accept: 'text/event-stream' }),
    body: JSON.stringify(payload), signal,
  }), receive),
  extract: (text: string, persist = true) =>
    request<{ tasks: ExtractedTask[]; document_id: string | null }>('/notes/extract', {
      method: 'POST',
      body: JSON.stringify({ text, persist }),
    }),
  todos: () => request<Todo[]>('/todos'),
  todoViews: () =>
    request<Record<'today' | 'this_week' | 'overdue' | 'waiting', Array<Record<string, string>>>>(
      '/todos/views',
    ),
  createTodo: (
    payload: Partial<Todo> & {
      title: string
      due_raw?: string
      remind_at?: string | null
      detail?: string
      source_quote?: string
      origin_document_id?: string
    },
  ) => request<Todo>('/todos', { method: 'POST', body: JSON.stringify(payload) }),
  updateTodo: (id: string, payload: Record<string, unknown>) =>
    request<Todo>(`/todos/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  progress: (payload: { work_done: string; todo_id?: string; observations?: string; blockers?: string }) =>
    request<{ id: string }>('/progress', { method: 'POST', body: JSON.stringify(payload) }),
  progressList: (opts?: { days?: number; start_date?: string; end_date?: string; limit?: number }) => {
    const q = new URLSearchParams()
    if (opts?.days) q.set('days', String(opts.days))
    if (opts?.start_date) q.set('start_date', opts.start_date)
    if (opts?.end_date) q.set('end_date', opts.end_date)
    if (opts?.limit) q.set('limit', String(opts.limit))
    const qs = q.toString()
    return request<{ items: ProgressEntry[]; start_date: string; end_date: string }>(
      `/progress${qs ? `?${qs}` : ''}`,
    )
  },
  weeklyReport: () =>
    request<{
      id: string
      period_start: string
      period_end: string
      body_markdown: string
      source_refs: Record<string, unknown>
      status: string
    }>('/reports/weekly', { method: 'POST', body: '{}' }),
  prepareEmail: (payload: { to_email: string; subject: string; body: string; session_id?: string }) =>
    request<ApprovalInfo>('/approvals/email', { method: 'POST', body: JSON.stringify(payload) }),
  decide: (payload: {
    approval_id: string
    decision: 'approved' | 'rejected'
    payload_version: string
    session_id?: string
  }) =>
    request<{
      ok: boolean
      status: string
      executed?: boolean
      error?: string
      graph_resume?: string
    }>('/chat/resume', {
      method: 'POST',
      body: JSON.stringify({ session_id: 'ui', ...payload }),
    }),
  getApproval: (id: string) => request<ApprovalInfo>(`/approvals/${id}`),
  knowledgeSearch: (query: string) =>
    request<KnowledgeResult>('/knowledge/search', {
      method: 'POST',
      body: JSON.stringify({ query }),
    }),
  zoteroConnection: () => request<ZoteroConnection>('/zotero/credential'),
  bindZotero: (apiKey: string) =>
    request<ZoteroConnection>('/zotero/credential', {
      method: 'POST',
      body: JSON.stringify({ api_key: apiKey }),
    }),
  disconnectZotero: () =>
    request<ZoteroConnection>('/zotero/credential', { method: 'DELETE' }),
  searchZotero: (query: string) =>
    request<{ items: ZoteroItem[] }>(`/zotero/search?q=${encodeURIComponent(query)}`),
  chat: (payload: {
    session_id: string
    messages: Array<{ role: string; content: string }>
    active_report_id?: string
    active_todo_id?: string
    active_approval_id?: string
  }) =>
    request<{
      reply: string
      session_id: string
      pending_approval_id?: string | null
      tools_used: string[]
      artifacts?: Array<{
        type: string
        id: string
        title?: string
        status?: string
        content_hash?: string
      }>
    }>('/chat', { method: 'POST', body: JSON.stringify(payload) }),
  newTask: () => request<{ thread_id: string }>('/chat/new-task', { method: 'POST' }),
  getReport: (id: string) =>
    request<{
      id: string
      period_start: string
      period_end: string
      body_markdown: string
      content_hash: string
      status: string
      source_refs: Record<string, unknown>
    }>(`/reports/${id}`),
  editReport: (id: string, body_markdown: string, content_hash: string) =>
    request<{ ok: boolean; id: string; content_hash: string; status: string }>(
      `/reports/${id}`,
      {
        method: 'PATCH',
        body: JSON.stringify({ body_markdown, content_hash }),
      },
    ),
  chatHistory: (sessionId: string) =>
    request<{
      messages: Array<{
        role: string
        content: string
        card?: Record<string, string>
        id?: string
        created_at?: string | null
      }>
      pending_approval_id?: string | null
      source?: string
    }>(`/chat/history?session_id=${encodeURIComponent(sessionId)}`),
  listThreads: () =>
    request<{
      threads: Array<{
        thread_id: string
        title: string
        created_at?: string | null
        updated_at?: string | null
      }>
    }>('/threads'),
  createThread: (thread_id: string, title?: string) =>
    request<{ thread_id: string; title: string }>('/threads', {
      method: 'POST',
      body: JSON.stringify({ thread_id, title }),
    }),
  deleteThread: (threadId: string) =>
    request<{ ok: boolean }>(`/threads/${threadId}`, { method: 'DELETE' }),
  renameThread: (threadId: string, title: string) =>
    request<{ thread_id: string; title: string }>(`/threads/${threadId}`, {
      method: 'PATCH', body: JSON.stringify({ title }),
    }),
  uploadDoc: async (file: File, docType = 'meeting_note', sessionId?: string) => {
    const form = new FormData()
    form.append('file', file)
    const params = new URLSearchParams({ doc_type: docType })
    if (sessionId) params.set('session_id', sessionId)
    const res = await fetch(`${BASE}/docs/upload?${params}`, {
      method: 'POST',
      headers: authHeaders(),
      body: form,
    })
    if (res.status === 401) handleUnauthorized()
    const data = await res.json()
    if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `上传失败（${res.status}）`)
    if (!data.document_id || !data.chunks) throw new Error('文件未成功入库，请检查文件内容')
    return data as { document_id: string; chunks: number }
  },
  docsList: () => request<DocumentInfo[]>('/docs'),
  docDetail: (id: string) => request<DocumentDetail>(`/docs/${id}`),
  deleteDoc: (id: string) =>
    request<{ ok: boolean; deleted_chunks: number }>(`/docs/${id}`, { method: 'DELETE' }),
  renameDoc: (id: string, title: string) =>
    request<{ ok: boolean; title: string }>(`/docs/${id}`, {
      method: 'PUT',
      body: JSON.stringify({ title }),
    }),
}
