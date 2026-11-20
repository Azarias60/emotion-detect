import { API_BASE, api } from './client'
import type { ReportCollection, ReportItem, ReportsGateway } from '../domain/reports'

export function createHttpReports(token: string): ReportsGateway {
  const withToken = { token }
  return {
    list: () => api<ReportCollection>('/reports', withToken),
    detail: (id: number) => api<ReportItem>(`/reports/${id}`, withToken),
    async remove(id: number) {
      await api<null>(`/reports/${id}`, { ...withToken, method: 'DELETE' })
    },
  }
}

const RECONNECT_DELAY_MS = 1500

/** 报告事实流：订阅建立即取得当前账号事实，随后接收创建/修改/删除。

返回结束入口；只结束本次订阅与连接，不改变报告或后台处理的使用期。
连接中断按同一身份自动重连，重连即重新取得当前事实。
*/
export function connectReportStream(token: string, onFacts: (items: ReportItem[]) => void): () => void {
  const controller = new AbortController()
  let stopped = false
  let reconnectTimer: number | null = null

  function scheduleReconnect() {
    if (stopped || reconnectTimer !== null) return
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null
      if (!stopped) void run()
    }, RECONNECT_DELAY_MS)
  }

  async function run() {
    try {
      const response = await fetch(`${API_BASE}/observations/reports`, {
        headers: { Authorization: `Bearer ${token}`, Accept: 'text/event-stream' },
        signal: controller.signal,
      })
      if (!response.ok || !response.body) throw new Error(`报告观察流连接失败（${response.status}）`)
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
            const facts: unknown = JSON.parse(event.slice(prefix.length))
            if (Array.isArray(facts)) onFacts(facts as ReportItem[])
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
