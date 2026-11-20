import { useState } from 'react'
import LoginPage from '../pages/LoginPage'
import type { AuthService, Session } from '../domain/auth'

export default function AuthScreen({ service, onAuthenticated }: {
  service: AuthService; onAuthenticated: (session: Session) => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  async function request<T>(work: () => Promise<T>): Promise<T | null> {
    if (busy) return null
    setBusy(true); setError(''); setMessage('')
    try { return await work() }
    catch (e) {
      setError(e instanceof Error ? e.message : '请求失败')
      return null
    } finally { setBusy(false) }
  }

  return <LoginPage busy={busy} error={error} message={message}
    onLogin={async (username, password) => {
      const session = await request(() => service.login(username, password))
      if (session) onAuthenticated(session)
    }}
    onRegister={async (username, password, confirm) => {
      const result = await request(() => service.register(username, password, confirm))
      if (result) setMessage('注册成功，请使用账号登录')
      return result !== null
    }}
  />
}
