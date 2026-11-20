import { api } from './client'
import type { AuthGateway, Session } from '../domain/auth'

export const httpAuth: AuthGateway = {
  login: (username, password) => api<Session>('/auth/login', { method: 'POST', body: { username, password } }),
  register: (username, password, rePassword) => api<{ id: number }>('/auth/register', {
    method: 'POST', body: { username, password, rePassword },
  }),
  logout: async token => { await api<null>('/auth/logout', { method: 'POST', token }) },
}
