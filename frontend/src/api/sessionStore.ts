import type { Session, SessionStore } from '../domain/auth'

const KEY = 'physiosense-v2-session'
export const browserSessionStore: SessionStore = {
  read() {
    try {
      const value: unknown = JSON.parse(sessionStorage.getItem(KEY) || 'null')
      if (value && typeof value === 'object' && 'token' in value && typeof value.token === 'string'
        && value.token && 'user' in value && value.user && typeof value.user === 'object'
        && 'username' in value.user && typeof value.user.username === 'string') return value as Session
    } catch { /* 无效或无法读取的会话不视为已登录 */ }
    return null
  },
  write(session) {
    try {
      if (session) sessionStorage.setItem(KEY, JSON.stringify(session))
      else sessionStorage.removeItem(KEY)
    } catch { /* 受限环境维持本页内存状态 */ }
  },
}
