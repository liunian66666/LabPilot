/** 登录态：JWT 与用户名存 localStorage。 */

const TOKEN_KEY = 'labpilot_token'
const USER_KEY = 'labpilot_username'

/** localStorage 键前缀，按用户名隔离各用户数据。 */
const NS_PREFIX = 'labpilot_'

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function getUsername(): string {
  return localStorage.getItem(USER_KEY) || ''
}

export function setAuth(token: string, username: string): void {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, username)
}

/**
 * 清除登录态（token + username），保留用户会话数据。
 * 下次同一用户登录时可恢复对话历史。
 */
export function clearAuth(): void {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

export function isLoggedIn(): boolean {
  return !!getToken()
}

/**
 * 返回按用户名命名空间化的 localStorage key。
 * 未登录时回退到 "guest"（不应在正常流程中发生）。
 *
 * @param suffix 业务 key 后缀，如 "sessions"、"ctx_v2"、"chat_{threadId}"
 */
export function storageKey(suffix: string): string {
  return `${NS_PREFIX}${getUsername() || 'guest'}_${suffix}`
}

/** 供裸 fetch（流式 / 上传）拼装鉴权头。 */
export function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const headers: Record<string, string> = { ...extra }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  return headers
}

/** 401：清登录态并跳登录页（保留原路径，登录后回跳）。 */
export function handleUnauthorized(): void {
  clearAuth()
  const here = window.location.pathname + window.location.search
  if (!window.location.pathname.startsWith('/login')) {
    window.location.href = `/login?redirect=${encodeURIComponent(here)}`
  }
}
