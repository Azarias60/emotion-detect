import { useEffect, useMemo, useSyncExternalStore } from 'react'
import { createLlmSettingsSession, type LlmSettingsSession } from '../application/llmSettings'
import type { LlmKeyDraft, LlmSettingsPort, LlmSettingsState } from '../domain/llmSettings'

export interface LlmSettingsBinding {
  state: LlmSettingsState
  save: (draft: LlmKeyDraft) => Promise<boolean>
  reload: () => void
}

/**
 * 模型 Key 的 React 绑定：进页面拉一次状态（只拿掩码），保存用同一个会话。
 * 没有定时器——配置不轮询。
 *
 * 刻意不做卸载 dispose：会话不持有定时器/订阅/网络句柄，而 StrictMode 会
 * 模拟一次卸载再挂载并复用同一个 useMemo 值——若卸载时标记销毁，复用后的
 * 会话就永久丢弃所有结果（实测表现为面板一直"读取中…"）。
 */
export function useLlmSettings(port: LlmSettingsPort): LlmSettingsBinding {
  const session: LlmSettingsSession = useMemo(() => createLlmSettingsSession(port), [port])
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot)

  useEffect(() => {
    void session.load()
  }, [session])

  return {
    state,
    save: session.save,
    reload: () => void session.load(),
  }
}
