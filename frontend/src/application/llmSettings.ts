/**
 * 模型 Key 会话（application 层）：读状态 / 保存 key。
 * 纯编排——网络在 api/llmSettings.ts，页面只管把绑定画出来。
 *
 * 保存成功后引擎下一句对话就换用新 key（LLMService 每轮新建），
 * 所以这里不需要重启、不需要重连，只把新状态写回并给一条确认。
 */
import {
  hasKeyToSave,
  validateKey,
  type LlmConfigPatch,
  type LlmKeyDraft,
  type LlmSettingsPort,
  type LlmSettingsState,
  initialLlmSettingsState,
} from '../domain/llmSettings'

export interface LlmSettingsSession {
  subscribe: (listener: () => void) => () => void
  getSnapshot: () => LlmSettingsState
  load: () => Promise<void>
  save: (draft: LlmKeyDraft) => Promise<boolean>
}

export function createLlmSettingsSession(port: LlmSettingsPort): LlmSettingsSession {
  let state = initialLlmSettingsState

  const listeners = new Set<() => void>()
  const emit = () => listeners.forEach((listener) => listener())
  const set = (patch: Partial<LlmSettingsState>) => {
    state = { ...state, ...patch }
    emit()
  }

  const load = async () => {
    set({ loading: true, error: '' })
    try {
      const config = await port.get()
      set({ config, loading: false })
    } catch (error) {
      set({ loading: false, error: `读取配置失败：${(error as Error)?.message ?? error}` })
    }
  }

  const save = async (draft: LlmKeyDraft) => {
    const invalid = validateKey(draft.apiKey)
    if (invalid) {
      set({ error: invalid, notice: '' })
      return false
    }
    if (!hasKeyToSave(draft)) {
      set({ notice: '请先填写 API Key', error: '' })
      return false
    }
    const patch: LlmConfigPatch = { apiKey: draft.apiKey.trim() }
    set({ saving: true, error: '', notice: '' })
    try {
      const { config, message } = await port.save(patch)
      set({ config, saving: false, notice: message })
      return true
    } catch (error) {
      set({ saving: false, error: `保存失败：${(error as Error)?.message ?? error}` })
      return false
    }
  }

  return {
    subscribe: (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    getSnapshot: () => state,
    load,
    save,
  }
}
