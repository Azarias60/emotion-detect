/**
 * 模型 Key 的平台实现（api）：引擎（8020）经 Vite 代理 /dh-api 访问。
 * key 只往下写、不往上读——GET 返回的永远是掩码。
 */
import type { LlmConfigView, LlmSettingsPort } from '../domain/llmSettings'

const BASE = '/dh-api'

interface Envelope {
  config?: LlmConfigView
  message?: string
  error?: string
  ok?: boolean
}

export function createLlmSettingsPort(): LlmSettingsPort {
  const read = async (response: Response): Promise<Envelope> => {
    const body = await response.json().catch(() => ({})) as Envelope
    if (!response.ok) throw new Error(body.message || body.error || `引擎返回 ${response.status}`)
    return body
  }

  return {
    async get() {
      const body = await read(await fetch(`${BASE}/api/llm/config`))
      if (!body.config) throw new Error('引擎没有返回配置')
      return body.config
    },

    async save(patch) {
      const body = await read(await fetch(`${BASE}/api/llm/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      }))
      if (!body.config) throw new Error('保存后没有拿到新配置')
      return { config: body.config, message: body.message || '已保存' }
    },
  }
}
