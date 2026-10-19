/** 前端短期上下文：按 thread 持久化，切换会话不串 active_* 对象。 */

import { getUsername, storageKey } from '../utils/auth'

export interface AppContext {
  thread_id: string
  active_report_id?: string
  active_todo_id?: string
  active_approval_id?: string
}

interface Store {
  current_thread_id: string
  threads: Record<string, AppContext>
}

function randomId() {
  return 'th_' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36)
}

/** 当前用户的上下文存储 key。 */
function ctxKey(): string {
  return storageKey('ctx_v2')
}

function loadStore(): Store {
  const key = ctxKey()
  try {
    const raw = localStorage.getItem(key)
    if (raw) {
      const s = JSON.parse(raw) as Store
      if (s.current_thread_id && s.threads) return s
    }
  } catch {
    /* ignore */
  }
  const thread_id = randomId()
  const fresh: Store = {
    current_thread_id: thread_id,
    threads: { [thread_id]: { thread_id } },
  }
  // 新建即保存
  saveStore(fresh)
  return fresh
}

function saveStore(s: Store) {
  localStorage.setItem(ctxKey(), JSON.stringify(s))
}

export function getContext(): AppContext {
  const s = loadStore()
  if (!s.threads[s.current_thread_id]) {
    s.threads[s.current_thread_id] = { thread_id: s.current_thread_id }
    // 首次创建 thread 必须立即落盘，否则刷新会换 ID 导致历史丢失
    saveStore(s)
  }
  return { ...s.threads[s.current_thread_id] }
}

export function saveContext(ctx: AppContext) {
  const s = loadStore()
  s.current_thread_id = ctx.thread_id
  s.threads[ctx.thread_id] = { ...ctx }
  saveStore(s)
}

export function newTaskContext(): AppContext {
  const s = loadStore()
  const thread_id = randomId()
  // 新任务：新 thread，无 active_*；旧线程上下文保留可切回
  s.current_thread_id = thread_id
  s.threads[thread_id] = { thread_id }
  saveStore(s)
  return { thread_id }
}

export function switchThread(thread_id: string): AppContext {
  const s = loadStore()
  if (!s.threads[thread_id]) {
    s.threads[thread_id] = { thread_id }
  }
  s.current_thread_id = thread_id
  saveStore(s)
  return { ...s.threads[thread_id] }
}

export function patchContext(patch: Partial<AppContext>) {
  const ctx = { ...getContext(), ...patch }
  saveContext(ctx)
  return ctx
}