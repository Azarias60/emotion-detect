/**
 * 数字人（陪伴）领域模型：引擎事件 → UI 状态的纯函数。
 *
 * 引擎（digital-human/，8020）通过 WebSocket 回传事件流，WebRTC 直接推送
 * 口型画面与语音。本文件只描述"事件长什么样、UI 该变成什么样"，
 * 不碰任何网络与浏览器 API——便于单测，也便于替换传输实现。
 */

export type ConnectionState = 'idle' | 'connecting' | 'connected' | 'failed'

export type MessageRole = 'user' | 'assistant' | 'action' | 'error'

/** 错误消息附带的"下一步操作"：conn=重连，mic=去开麦克风。 */
export type MessageRetry = 'conn' | 'mic'

export interface ChatMessage {
  id: number
  role: MessageRole
  content: string
  retry?: MessageRetry
}

/** 流程条：0 待机 / 1 在听·识别 / 2 思考 / 3 合成 / 4 说话。 */
export type PipelineStage = 0 | 1 | 2 | 3 | 4

export interface EmotionBadge {
  key: string
  label: string
  hint: string
  consecutive: number
  /** 本次是否相对上一句翻转（翻转时给用户一条轻提示） */
  changed: boolean
}

export interface Recommendation {
  kind: string
  key: string
  reason: string
}

export interface DocCard {
  title: string
  points: string[]
}

/** 引擎素材库（/api/assets）：数字人列表一项 = 形象 + 音色的固定搭配。 */
export interface VoiceInfo {
  id: string
  name: string
}

export interface AvatarPreset {
  id: string
  name: string
  avatar: string
  voice: string
  note: string
  ready: boolean
  reason?: string
}

export interface AvatarInfo {
  id: string
  dance?: boolean
}

export interface DropVideo {
  name: string
  file: string
  built: boolean
  generating?: boolean
}

export interface GenerationJob {
  name: string
  status: string
  message?: string
}

export interface EngineAssets {
  avatars: AvatarInfo[]
  defaultAvatar: string
  voices: VoiceInfo[]
  activeVoice: string
  presets: AvatarPreset[]
  dropVideos: DropVideo[]
  generations: GenerationJob[]
}

/** 当前播放片段（引擎状态机，待机框展示播放逻辑）。 */
export interface SegmentInfo {
  segment: string
  kind: string
}

/** 链路诊断（api 层采集，仅展示用）：音量只证明"有输入"，上行字节只证明"发出去了"。 */
export interface CompanionDiag {
  micLevel: number
  uplinkBytes: number
  engineEvents: number
  audioBlocked: boolean
}

export interface DigitalHumanState {
  connection: ConnectionState
  connectionDetail: string
  messages: ChatMessage[]
  busy: boolean
  speaking: boolean
  pipeline: PipelineStage
  /** 麦克风是否正在采集（用户意图 + 实际打开） */
  listening: boolean
  emotion: EmotionBadge | null
  recommendation: Recommendation | null
  docCard: DocCard | null
  /** 消息id 自增计数（进 state 保证纯函数可重放） */
  nextId: number
  /** 打断待确认：插话后转写回来时给"好的，你说" */
  bargeInPending: boolean
  /** 引擎素材库（数字人/音色/待生成素材） */
  assets: EngineAssets | null
  /** 当前选择的形象 id（重连时带给 /offer） */
  currentAvatar: string
  /** 当前播放片段（引擎状态机） */
  segment: SegmentInfo | null
  /** 切换数字人中：teardown 已清视频、新连接未建成 */
  switching: boolean
  /** 舞蹈预加载中 */
  dancing: boolean
  /** 浏览器拦了自动播放：需要用户点一下"点击开播" */
  needPlayTap: boolean
  /** 字幕条是否还在展示"刚说的内容"（停止 10 秒后回引导语） */
  captionFresh: boolean
  /** 开麦后 5 秒音量始终为 0：提示检查权限/设备 */
  micSilent: boolean
  diag: CompanionDiag
}

export const initialDigitalHumanState: DigitalHumanState = {
  connection: 'idle',
  connectionDetail: '',
  messages: [],
  busy: false,
  speaking: false,
  pipeline: 0,
  listening: false,
  emotion: null,
  recommendation: null,
  docCard: null,
  nextId: 1,
  bargeInPending: false,
  assets: null,
  currentAvatar: '',
  segment: null,
  switching: false,
  dancing: false,
  needPlayTap: false,
  captionFresh: false,
  micSilent: false,
  diag: { micLevel: 0, uplinkBytes: 0, engineEvents: 0, audioBlocked: false },
}

/** 传输层上报的诊断快照（api 层自带采集节奏）。 */
export interface TransportDiag {
  micLevel: number
  uplinkBytes: number
  engineEvents: number
  audioBlocked: boolean
}

/** 应用层向平台层索取的能力（端口由契约方定义，实现在 api 层）。 */
export interface DigitalHumanTransport {
  ready(): Promise<boolean>
  /** avatar 非空时随 offer 下发：换形象重连按该 id 建会话 */
  openRtc(video: HTMLVideoElement, avatar?: string): Promise<string>
  openEvents(sessionId: string, onEvent: (event: EngineEvent) => void, onClose: () => void): void
  send(payload: Record<string, unknown>): void
  sendPcm(buffer: ArrayBuffer): void
  startMic(sendPcm: (buffer: ArrayBuffer) => void): Promise<void>
  stopMic(): void
  /** 恢复被浏览器自动播放策略挂起的采集上下文（首次点击/切回标签页时调用） */
  resume?(): void
  isSpeaking(sessionId: string): Promise<boolean>
  /** 只断画面/事件通道（换形象重连用），不动麦克风 */
  closeMedia(): void
  /** 引擎素材库；引擎不在时返回 null（页面保持空态） */
  assets(): Promise<EngineAssets | null>
  setVoice(id: string): Promise<string | null>
  generateAvatar(name: string): Promise<string>
  deleteAvatar(id: string): Promise<string>
  segment(sessionId: string): Promise<SegmentInfo | null>
  dance(sessionId: string): Promise<{ ok: boolean; message?: string }>
  /** 双保险打断：WS cancel 之外再打 /interrupt_talk 确保播报立即停 */
  interruptTalk(sessionId: string): void
  /** WS 断开时的兜底：/human 直接投递文字（无流式回显） */
  humanText(sessionId: string, text: string): void
  personas(): Promise<{ personas: { id: string; name: string }[]; active: string } | null>
  setPersona(id: string): Promise<void>
  /** 音量/上行/引擎回传计数（api 层自带节奏，150ms 推一次） */
  onDiagnostics(handler: (diag: TransportDiag) => void): () => void
  closeAll(): void
}

export type EngineEvent =
  | { type: 'configured' }
  | { type: 'hint'; message?: string }
  | { type: 'awake' }
  | { type: 'wake_word_pending'; wakeWord?: string }
  | { type: 'wake' }
  | { type: 'emotion'; emotion: string; label: string; hint?: string; consecutive?: number; changed?: boolean }
  | { type: 'doc'; title: string; points: string[] }
  | { type: 'recommend'; kind?: string; module?: string; reason?: string; emotion?: string }
  | { type: 'speech_started' }
  | { type: 'speech_stopped' }
  | { type: 'pipeline'; stage: 'asr' | 'llm' }
  | { type: 'transcript'; text: string }
  | { type: 'assistant_delta'; text: string }
  | { type: 'audio_start' }
  | { type: 'tts_fallback' }
  | { type: 'action'; action: string }
  | { type: 'response_done' }
  | { type: 'response_accepted' }
  | { type: 'session_reset' }
  | { type: 'error'; message?: string }

/** 文案表：所有面向用户的提示语集中在此，改话术只动这一处。 */
export const TEXTS = {
  wakeHint: (word?: string) => `说「${word || '你好'}」唤醒数字人`,
  awake: '已唤醒，请直接说',
  bargeIn: '好的，你说',
  sessionReset: '会话已重新开始，我们重新聊',
  ttsFallback: '在线语音暂不可用，本段改用系统语音',
  engineReady: '数字人已就绪，开始聊天吧',
  engineLoading: '数字人模型加载中（首次约半分钟）…',
  engineUnreachable: '数字人服务暂时没连上，请稍等几秒后重试；若多次失败请联系管理员',
  micDenied: '麦克风权限被拒绝：点浏览器地址栏左侧的锁图标 → 把麦克风改为"允许"，然后重试',
  micBusy: '麦克风被占用：请关闭其他打开了本系统的标签页，再点麦克风按钮',
  micMissing: '没有检测到麦克风设备：请插入麦克风，或在系统设置里选择输入设备',
  micGeneric: '麦克风开启失败，请再点一次麦克风按钮重试',
  micNoPrompt: '没有收到麦克风授权（浏览器可能已静默拒绝）：点地址栏的锁图标把麦克风改为"允许"，再点一次麦克风按钮',
  micClosed: '麦克风已关闭',
  micOpenWithWake: '麦克风已开启，说「你好」唤醒数字人',
  micOpenAwake: '麦克风已开启，说完停顿一下自动识别',
  voicePaired: (voice: string) => `音色已按配对自动切到「${voice}」（形象与音色强绑定）`,
  voiceManual: (name: string) => `已手动切换音色到「${name}」（与当前形象的默认配对不同）`,
} as const

const MAX_MESSAGES = 200

/** 追加一条消息（供应用层在领域事件之外直接推错误/提示时复用）。 */
export function pushMessage(
  state: DigitalHumanState,
  role: MessageRole,
  content: string,
  retry?: MessageRetry,
): DigitalHumanState {
  const messages = [...state.messages, { id: state.nextId, role, content, ...(retry ? { retry } : {}) }]
  return {
    ...state,
    messages: messages.length > MAX_MESSAGES ? messages.slice(messages.length - MAX_MESSAGES) : messages,
    nextId: state.nextId + 1,
  }
}

/**
 * 引擎事件 → UI 状态。同一轮的 transcript/assistant_delta 需要"追加到当前气泡"，
 * 因此这里用 id 追踪：用户气泡取最后一条 user，助手气泡取最后一条 assistant。
 */
export function reduceDigitalHuman(state: DigitalHumanState, event: EngineEvent): DigitalHumanState {
  switch (event.type) {
    case 'configured':
      return { ...state, connection: 'connected', connectionDetail: '实时数字人已连接' }

    case 'hint':
      return event.message ? pushMessage(state, 'action', event.message) : state

    case 'awake':
      return { ...state, connectionDetail: '麦克风已开启，直接说就好' }

    case 'wake_word_pending':
      return pushMessage(state, 'action', TEXTS.wakeHint(event.wakeWord))

    case 'wake':
      return pushMessage(state, 'action', TEXTS.awake)

    case 'emotion': {
      const next: DigitalHumanState = {
        ...state,
        emotion: {
          key: event.emotion,
          label: event.label,
          hint: event.hint ?? '',
          consecutive: event.consecutive ?? 1,
          changed: !!event.changed,
        },
      }
      // 情绪翻转给一条轻提示（连续同情绪不重复刷屏，与原版同口径）
      if (event.changed && event.hint) return pushMessage(next, 'action', event.hint)
      return next
    }

    case 'doc':
      return { ...state, docCard: { title: event.title, points: event.points } }

    case 'recommend': {
      const next: DigitalHumanState = {
        ...state,
        recommendation: {
          kind: event.kind ?? 'module',
          key: event.module || event.emotion || '',
          reason: event.reason ?? '',
        },
      }
      // 建议进聊天流（灰字）：推荐卡只在对应页面展示（陪伴页只留舞蹈邀请）
      return event.reason ? pushMessage(next, 'action', `建议：${event.reason}`) : next
    }

    case 'speech_started': {
      // 抢话打断确认：数字人正在播报时被插话，转写回来时给一句"好的，你说"
      const bargeInPending = state.speaking || state.busy
      return { ...state, busy: true, speaking: false, pipeline: 1, bargeInPending }
    }

    case 'speech_stopped':
      return { ...state, pipeline: 1, connectionDetail: '正在识别你的语音…' }

    case 'pipeline':
      return { ...state, pipeline: event.stage === 'asr' ? 1 : 2 }

    case 'transcript': {
      let next = state
      if (state.bargeInPending) {
        next = pushMessage({ ...next, bargeInPending: false }, 'action', TEXTS.bargeIn)
      }
      const messages = [...next.messages]
      // 覆盖当前待定的用户气泡（同一轮可能补发更全的识别结果），不堆叠
      for (let i = messages.length - 1; i >= 0; i -= 1) {
        if (messages[i].role === 'user') {
          messages[i] = { ...messages[i], content: event.text }
          return { ...next, messages, busy: true, pipeline: 2 }
        }
        if (messages[i].role !== 'assistant') break
      }
      return { ...pushMessage(next, 'user', event.text), busy: true, pipeline: 2 }
    }

    case 'assistant_delta': {
      const messages = [...state.messages]
      const last = messages[messages.length - 1]
      if (last && last.role === 'assistant') {
        messages[messages.length - 1] = { ...last, content: last.content + event.text }
        return { ...state, messages }
      }
      return { ...pushMessage(state, 'assistant', event.text) }
    }

    case 'audio_start':
      return { ...state, pipeline: 4, busy: false, speaking: true, connectionDetail: '数字人正在说话…' }

    case 'tts_fallback':
      return pushMessage(state, 'action', TEXTS.ttsFallback)

    case 'action':
      return pushMessage(state, 'action', `动作 · ${event.action}`)

    case 'response_done':
    case 'response_accepted':
      return { ...state, busy: false }

    case 'session_reset':
      return {
        ...initialDigitalHumanState,
        connection: state.connection,
        connectionDetail: TEXTS.sessionReset,
        listening: state.listening,
        diag: state.diag,
        nextId: state.nextId,
      }

    case 'error':
      return pushMessage(
        { ...state, busy: false, speaking: false, pipeline: 0 },
        'error',
        event.message || '处理失败',
        'conn',
      )

    default:
      return state
  }
}

/** 连续"没听清"话术阶梯（引擎侧同款）：前端据此把 hint 渲染成不同强调色。 */
export function emptyTranscriptHint(streak: number): string {
  if (streak <= 1) return '没听清，请再说一次'
  if (streak === 2) return '还是没听清，请靠近麦克风、放慢语速再说一次'
  return '一直没听清，请检查浏览器麦克风权限，或换个麦克风设备试试'
}

/** 引擎内部段名（idle2/talk/greet/idle2_to_greet…）→ 用户可读名。 */
export function friendlySegment(segment: string): string {
  if (/_to_/.test(segment)) return '状态切换'
  if (/^idle/.test(segment)) return '待机'
  if (/^talk/.test(segment)) return '说话'
  const named: Record<string, string> = {
    greet: '打招呼', comfort: '安慰', encourage: '鼓励', praise: '表扬', dance: '跳舞',
  }
  return named[segment] ?? segment
}
