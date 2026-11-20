import { API_BASE, api } from './client'
import type { DigitalHumanObject } from '../domain/digitalHuman'

export type ObjectKind = 'configuration' | 'avatar' | 'voice' | 'material'

export type ObjectReferences = { kind: string; id: number; name?: string }

export function createObjectGateway(token: string) {
  const withToken = { token }
  return {
    list: (kind: ObjectKind) => api<{ items: DigitalHumanObject[] }>(`/objects/${kind}`, withToken),
    create: (kind: ObjectKind, values: Record<string, unknown>) =>
      api<DigitalHumanObject>(`/objects/${kind}`, { ...withToken, method: 'POST', body: values }),
    update: (kind: ObjectKind, id: number, values: Record<string, unknown>) =>
      api<DigitalHumanObject>(`/objects/${kind}/${id}/update`, { ...withToken, method: 'POST', body: values }),
    remove: (kind: ObjectKind, id: number) =>
      api<null>(`/objects/${kind}/${id}/delete`, { ...withToken, method: 'POST' }),
    references: (kind: ObjectKind, id: number) =>
      api<{ references: ObjectReferences[] }>(`/objects/${kind}/${id}/references`, withToken),
  }
}

export type ObjectGateway = ReturnType<typeof createObjectGateway>

/** 对象集合事实流：订阅建立即取得当前集合，随后随创建/修改/删除更新。 */
export function connectObjectStream(token: string, kind: ObjectKind, onRows: (rows: DigitalHumanObject[]) => void): () => void {
  const controller = new AbortController()
  let stopped = false
  let reconnectTimer: number | null = null

  function scheduleReconnect() {
    if (stopped || reconnectTimer !== null) return
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null
      if (!stopped) void run()
    }, 1500)
  }

  async function run() {
    try {
      const response = await fetch(`${API_BASE}/observations/objects/${kind}`, {
        headers: { Authorization: `Bearer ${token}`, Accept: 'text/event-stream' },
        signal: controller.signal,
      })
      if (!response.ok || !response.body) throw new Error(`对象观察流连接失败（${response.status}）`)
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffered = ''
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffered += decoder.decode(value, { stream: true })
        let boundary = buffered.indexOf('\n\n')
        while (boundary !== -1) {
          const event = buffered.slice(0, boundary)
          buffered = buffered.slice(boundary + 2)
          const prefix = 'data: '
          if (event.startsWith(prefix)) {
            const rows: unknown = JSON.parse(event.slice(prefix.length))
            if (Array.isArray(rows)) onRows(rows as DigitalHumanObject[])
          }
          boundary = buffered.indexOf('\n\n')
        }
      }
    } catch (error) {
      if (controller.signal.aborted) return
      void error
    }
    scheduleReconnect()
  }

  void run()
  return () => {
    stopped = true
    if (reconnectTimer !== null) window.clearTimeout(reconnectTimer)
    controller.abort()
  }
}

export function createConversationGateway(token: string) {
  const withToken = { token }
  return {
    expressUsage: (kind: 'guide' | 'companion', active: boolean) =>
      api<null>(`/conversation/${kind}/usage`, { ...withToken, method: 'POST', body: { active } }).then(() => undefined),
    send: (kind: 'guide' | 'companion', text: string) =>
      api<{ reply: string }>(`/conversation/${kind}/send`, { ...withToken, method: 'POST', body: { text } }),
    restart: (kind: 'guide' | 'companion') =>
      api<null>(`/conversation/${kind}/restart`, { ...withToken, method: 'POST' }).then(() => undefined),
  }
}

export type ConversationGateway = ReturnType<typeof createConversationGateway>
export type ModelConfigGateway = ReturnType<typeof createModelConfigGateway>
export type ProductionGateway = ReturnType<typeof createProductionGateway>

export type SessionGateways = {
  objects: ObjectGateway
  conversation: ConversationGateway
  modelConfig: ModelConfigGateway
  production: ProductionGateway
}

export function createModelConfigGateway(token: string) {
  const withToken = { token }
  return {
    read: () => api<{ configured: boolean; masked: string }>('/model-config', withToken),
    write: (apiKey: string) =>
      api<null>('/model-config', { ...withToken, method: 'POST', body: { apiKey } }).then(() => undefined),
  }
}

export function createProductionGateway(token: string) {
  const withToken = { token }
  return {
    start: (kind: 'avatar' | 'voice', input: Record<string, unknown>) =>
      api<{ taskId: string }>(`/production/${kind}`, { ...withToken, method: 'POST', body: input }),
    status: (taskId: string) => api<{ status: string; failure: string }>(`/production/tasks/${taskId}`, withToken),
    cancel: (taskId: string) =>
      api<null>(`/production/tasks/${taskId}/cancel`, { ...withToken, method: 'POST' }).then(() => undefined),
    startRecording: () => api<null>('/recording/start', { ...withToken, method: 'POST' }).then(() => undefined),
    stopRecording: () => api<{ taskId: string }>('/recording/stop-submit', { ...withToken, method: 'POST' }),
    cancelRecording: () => api<null>('/recording/cancel', { ...withToken, method: 'POST' }).then(() => undefined),
  }
}
