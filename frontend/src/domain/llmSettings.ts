/**
 * 模型 Key 配置（领域模型）：页面只负责"填 key"这一件事。
 *
 * key 由页面写进引擎本机文件（data/llm_config.json，已被 .gitignore 挡住），
 * 代码里不留任何密钥；地址与模型名沿用引擎默认，页面不暴露、也不允许改。
 *
 * 引擎（digital-human/，8020）：
 *   GET  /api/llm/config   读状态（只回掩码，永不回明文）
 *   POST /api/llm/config   写入 key（下一句对话即生效）
 */

/** 生效状态：key 永远只有掩码。 */
export interface LlmConfigView {
  baseUrl: string
  model: string
  hasKey: boolean
  /** 掩码，如 "sk-…ced6"；未配置时为空串 */
  keyHint: string
  /** 被页面配置覆盖的字段，空数组=全用命令行值 */
  overrides: string[]
  /** page=页面填的 / local=命令行或环境变量 / unset=没有 */
  source: string
  /** 地址指向本机端点（Ollama 等）：不需要 key */
  localEndpoint: boolean
}

export interface LlmSettingsState {
  config: LlmConfigView | null
  loading: boolean
  saving: boolean
  error: string
  notice: string
}

export const initialLlmSettingsState: LlmSettingsState = {
  config: null,
  loading: false,
  saving: false,
  error: '',
  notice: '',
}

/** 页面表单：只有一个 key。留空 = 保持不变。 */
export interface LlmKeyDraft {
  apiKey: string
}

/** 提交给引擎的补丁。 */
export interface LlmConfigPatch {
  apiKey: string
}

/** 是否需要提交：key 留空就没有可保存的内容。 */
export function hasKeyToSave(draft: LlmKeyDraft): boolean {
  return draft.apiKey.trim().length > 0
}

/** key 来源的人话说明（页面上只显示掩码）。 */
export function describeSource(config: LlmConfigView | null): string {
  if (!config) return '读取中…'
  if (!config.hasKey) return config.localEndpoint ? '本机模型，无需 Key' : '未配置 Key'
  if (config.source === 'page') return `已配置 · ${config.keyHint}`
  return `已配置（命令行 / 环境变量）· ${config.keyHint}`
}

/** 校验：太短必然是粘贴错误，早点拦住比让引擎报鉴权失败清楚。返回错误文案或空串。 */
export function validateKey(apiKey: string): string {
  const text = apiKey.trim()
  if (text && text.length < 8) return 'API Key 看起来太短，请检查是否粘贴完整'
  return ''
}

/**
 * 模型 Key 端口（由契约方定义，实现在 api/llmSettings.ts）：
 * 应用层只依赖本接口，不碰 fetch。
 */
export interface LlmSettingsPort {
  get(): Promise<LlmConfigView>
  save(patch: LlmConfigPatch): Promise<{ config: LlmConfigView; message: string }>
}
