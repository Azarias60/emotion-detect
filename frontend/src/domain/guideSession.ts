/**
 * 导览页（暖暖）领域模型：default 模式引擎事件 → UI 状态的纯函数。
 *
 * 与情感数字人页（digitalHuman.ts，live 模式）的区别：无 WebRTC 画面，
 * 暖暖的语音由引擎按段以 WS 二进制 PCM 回传、浏览器播放；多了模块推荐
 * 确认跳转（引擎判定"好/可以"后发 module_jump，页面负责导航）。
 * 本文件不碰网络/音频，纯函数可单测。
 */

export type GuideConnection = 'idle' | 'connecting' | 'connected' | 'failed'

export interface HeardEntry {
  id: number
  role: 'user' | 'assistant' | 'system'
  text: string
  /** 系统条目里的错误（渲染成红色 + 重试） */
  error?: boolean
}

export interface GuideDoc {
  title: string
  points: string[]
  /** 可选导语（引擎资料卡与默认指引都可能带） */
  desc?: string
}

export interface GuideRecommend {
  module: string
  reason: string
}

export interface GuideState {
  connection: GuideConnection
  connectionDetail: string
  /** 对话记录（用户说的话 / 暖暖的回复 / 系统提示） */
  heard: HeardEntry[]
  nextId: number
  /** 识别/思考中 */
  busy: boolean
  /** 暖暖语音播放中（ mouth/状态条据此点亮） */
  speaking: boolean
  /** 麦克风采集中 */
  listening: boolean
  /** 唤醒门已开（第一句不必喊"你好"） */
  awake: boolean
  doc: GuideDoc | null
  recommend: GuideRecommend | null
  /** 待跳转的模块（引擎确认词判定后发出，页面据此导航） */
  moduleJump: GuideRecommend | null
}

/** 资料栏默认卡：悬停常驻，被引擎回答的资料卡替换；会话重置后回到这张 */
export const GUIDE_DEFAULT_DOC: GuideDoc = {
  title: '如何获得准确结果',
  desc: '开始检测前，请确认以下几点',
  points: [
    '光线充足，正脸对镜头，不遮挡',
    '检测前静坐 30 秒，保持自然表情',
    '语音请说普通话，环境保持安静',
    '疲劳与情绪检测约 1 分钟，期间不要切走页面',
    '健康问诊先拍舌象，再回答几道问诊题',
    '结果仅供健康参考，不能替代医生诊断',
  ],
}

export const initialGuideState: GuideState = {
  connection: 'idle',
  connectionDetail: '',
  heard: [],
  nextId: 1,
  busy: false,
  speaking: false,
  listening: false,
  awake: false,
  doc: GUIDE_DEFAULT_DOC,
  recommend: null,
  moduleJump: null,
}

/** 导览面板消费的视图绑定：app 层实现，页面只依赖这个签名（pages 不能反向依赖 app）。 */
export interface GuideBinding {
  state: GuideState
  sendText: (text: string) => void
  toggleMic: () => void
  /** 停止生成：打断回复并停语音回放 */
  interrupt: () => void
  resetSession: () => void
}

export type GuideEvent =
  | { type: 'configured' }
  | { type: 'hint'; message?: string; quiet?: boolean }
  | { type: 'wake_word_pending'; wakeWord?: string }
  | { type: 'wake' }
  | { type: 'transcript'; text: string }
  | { type: 'assistant_delta'; text: string }
  | { type: 'response_done' }
  | { type: 'doc'; title: string; points: string[] }
  | { type: 'recommend'; kind?: string; module?: string; reason?: string }
  | { type: 'module_jump'; module?: string; reason?: string }
  | { type: 'session_reset' }
  | { type: 'mic_taken' }
  | { type: 'error'; message?: string }

/** 应用层动作（非引擎事件）：音频播放边沿、麦克风开关。 */
export type GuideAction =
  | { type: 'playback'; active: boolean }
  | { type: 'mic'; on: boolean }
  | { type: 'connection'; value: GuideConnection; detail?: string }

export const GUIDE_TEXTS = {
  wakeHint: (word?: string) => `说「${word || '你好'}」唤醒导览员`,
  awake: '已唤醒，请直接说',
  sessionReset: '会话已重新开始',
  engineLoading: '数字人模型加载中（首次约半分钟）…',
  engineReady: '暖暖已就绪，打个招呼吧',
  engineUnreachable: '导览服务暂时没连上，请稍等几秒后重试',
  micDenied: '麦克风权限被拒绝：点浏览器地址栏左侧的锁图标 → 把麦克风改为"允许"，然后重试',
  micBusy: '麦克风被占用：请关闭其他打开了本系统的标签页，再点麦克风按钮',
  micMissing: '没有检测到麦克风设备：请插入麦克风，或在系统设置里选择输入设备',
  micGeneric: '麦克风开启失败，请再点一次麦克风按钮重试',
  micNoPrompt: '没有收到麦克风授权（浏览器可能已静默拒绝）：点地址栏的锁图标把麦克风改为"允许"，再点一次麦克风按钮',
  micTaken: '麦克风已被其他页面接管；回到本页会自动取回',
  jumpConfirm: (module: string) => `好的，这就带你去「${module}」`,
} as const

const MAX_HEARD = 120

/** 追加一条对话记录（应用层复用）。 */
export function pushGuideHeard(
  state: GuideState,
  role: HeardEntry['role'],
  text: string,
  error = false,
): GuideState {
  const heard = [...state.heard, { id: state.nextId, role, text, ...(error ? { error } : {}) }]
  return {
    ...state,
    heard: heard.length > MAX_HEARD ? heard.slice(heard.length - MAX_HEARD) : heard,
    nextId: state.nextId + 1,
  }
}

export function createGuideTransportOptions(): { onPlayback: (active: boolean) => void } {
  return { onPlayback: () => undefined }
}

/** 导览传输端口（default 模式）：契约在此，实现在 api 层。 */
export interface GuideTransport {
  /** 注册语音播放边沿回调（开始说话/说完），供状态条与口型动画点亮；返回解绑函数 */
  onPlayback(edge: (active: boolean) => void): () => void
  ready(): Promise<boolean>
  /**
   * 打开事件流。引擎下行语音 PCM 由传输层自行排程播放
   * （api 层持有 AudioContext），播放边沿经 onPlayback 上报；
   * 因此这里只需事件回调与断线回调。
   */
  openEvents(
    onEvent: (event: GuideEvent) => void,
    onClose: () => void,
  ): void
  send(payload: Record<string, unknown>): void
  sendPcm(buffer: ArrayBuffer): void
  startMic(sendPcm: (buffer: ArrayBuffer) => void): Promise<void>
  stopMic(): void
  /** 恢复被浏览器自动播放策略挂起的采集上下文（首次点击/切回标签页时调用） */
  resume?(): void
  /** 立即停止语音播放（打断/导航离开时调用） */
  stopPlayback(): void
  /** 离开首页时抑制语音外放（WS 继续收事件，只丢弃 PCM）；回到首页恢复 */
  suppressPlayback?(on: boolean): void
  closeAll(): void
}

function hear(state: GuideState, role: HeardEntry['role'], text: string, error = false): GuideState {
  const heard = [...state.heard, { id: state.nextId, role, text, ...(error ? { error } : {}) }]
  return {
    ...state,
    heard: heard.length > MAX_HEARD ? heard.slice(heard.length - MAX_HEARD) : heard,
    nextId: state.nextId + 1,
  }
}

export function reduceGuide(state: GuideState, event: GuideEvent): GuideState {
  switch (event.type) {
    case 'configured':
      return { ...state, connection: 'connected', connectionDetail: '暖暖已连接，说「你好」开始' }

    case 'hint':
      return event.message ? hear(state, 'system', event.message) : state

    case 'wake_word_pending':
      return hear(state, 'system', GUIDE_TEXTS.wakeHint(event.wakeWord))

    case 'wake':
      return { ...hear(state, 'system', GUIDE_TEXTS.awake), awake: true }

    case 'transcript':
      return hear({ ...state, busy: false }, 'user', event.text)

    case 'assistant_delta': {
      const heard = [...state.heard]
      const last = heard[heard.length - 1]
      if (last && last.role === 'assistant') {
        heard[heard.length - 1] = { ...last, text: last.text + event.text }
        return { ...state, heard }
      }
      return hear(state, 'assistant', event.text)
    }

    case 'response_done':
      return { ...state, busy: false }

    case 'doc':
      return { ...state, doc: { title: event.title, points: event.points, desc: '来自暖暖的整理，可随时提问追问' } }

    case 'recommend':
      if (event.kind !== 'module' || !event.module) return state
      return {
        ...hear(state, 'system', `建议：${event.reason ?? ''}`),
        recommend: { module: event.module, reason: event.reason ?? '' },
      }

    case 'module_jump':
      return {
        ...hear(
          { ...state, moduleJump: { module: event.module ?? '', reason: event.reason ?? '' } },
          'system',
          GUIDE_TEXTS.jumpConfirm(event.module ?? ''),
        ),
      }

    case 'session_reset':
      return {
        ...initialGuideState,
        connection: state.connection,
        connectionDetail: GUIDE_TEXTS.sessionReset,
        listening: state.listening,
        awake: state.awake,
        nextId: state.nextId,
      }

    case 'mic_taken':
      return {
        ...state,
        listening: false,
        connectionDetail: GUIDE_TEXTS.micTaken,
      }

    case 'error':
      return hear(
        { ...state, busy: false, speaking: false },
        'system',
        event.message || '处理失败',
        true,
      )

    default:
      return state
  }
}

export function applyGuideAction(state: GuideState, action: GuideAction): GuideState {
  switch (action.type) {
    case 'playback':
      return { ...state, speaking: action.active }
    case 'mic':
      return { ...state, listening: action.on }
    case 'connection':
      return { ...state, connection: action.value, ...(action.detail ? { connectionDetail: action.detail } : {}) }
    default:
      return state
  }
}
