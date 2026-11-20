// 登录后的唯一身份契约：后端签发 JWT 与用户资料。
export interface UserProfile {
  id: number; username: string; gender: string; age: number | null
  email: string; userPic: string; createTime: string; updateTime: string
}
export interface Session { token: string; user: UserProfile }
export interface AuthGateway {
  login(username: string, password: string): Promise<Session>
  register(username: string, password: string, confirm: string): Promise<{ id: number }>
  logout(token: string): Promise<void>
}
export interface SessionStore {
  read(): Session | null
  write(session: Session | null): void
}

// 上层策略消费端口，实际 HTTP 和持久化实现由组合入口注入。
export function createSessionService(gateway: AuthGateway, store: SessionStore) {
  function accept(session: Session) {
    if (!session.token || !session.user || typeof session.user.username !== 'string')
      throw new Error('后端未返回有效登录会话')
    store.write(session)
    return session
  }
  return {
    current: () => store.read(),
    login: async (username: string, password: string) => accept(await gateway.login(username, password)),
    register: (username: string, password: string, confirm: string) => gateway.register(username, password, confirm),
    logout: async (session: Session | null) => {
      try { if (session) await gateway.logout(session.token) }
      finally { store.write(null) }
    },
  }
}

export type AuthService = ReturnType<typeof createSessionService>
