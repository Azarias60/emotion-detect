/**
 * 导览会话（application 层）：default 模式编排——就绪探测、事件归约、
 * 空闲刷新、模块跳转回调。零平台 API（传输与音频播放由上层注入）。
 */
import {
  GUIDE_TEXTS,
  applyGuideAction,
  initialGuideState,
  pushGuideHeard,
  reduceGuide,
  type GuideEvent,
  type GuideState,
  type GuideTransport,
} from '../domain/guideSession'/** 空闲自动刷新：与情感数字人页同口径（3 分钟） */
export const GUIDE_IDLE_RESET_MS = 3 * 60 * 1000

export interface GuideSessionOptions {
  /** 引擎判定确认词后回调（页面据此导航到对应模块） */
  onModuleJump: (module: string) => void
  pageToken: string
}

export interface GuideSession {
  subscribe: (listener: () => void) => () => void
  getSnapshot: () => GuideState
  connect: () => void
  /** 页面重新挂载（SPA 返回首页）：复用仍活着的 WS，只恢复麦克风与播放状态 */
  attach: () => void
  /** SPA 离开首页：停麦 + 停语音，但保留 WS 与引擎侧会话记忆 */
  suspend: () => void
  tick: (now: number) => void
  /** 语音播放边沿（由 app 层把传输层的 onPlayback 接进来） */
  setPlayback: (active: boolean) => void
  sendText: (text: string) => void
  toggleMic: () => Promise<void>
  /** 停止生成：打断引擎当前回复并停掉本地语音回放（ChatGPT 式"停止"按钮） */
  interrupt: () => void
  /** 重新开始（清对话；引擎侧同义口令"重新开始"也走这里的效果） */
  resetSession: () => void
  dispose: () => void
}

export function createGuideSession(
  transport: GuideTransport,
  options: GuideSessionOptions,
): GuideSession {
  let state = initialGuideState
  let disposed = false
  let connected = false
  let connecting: Promise<void> | null = null
  let micOn = false
  // 麦克风常开（与原 Vue 版同口径）：连接后自动开麦，唤醒门保持待命；
  // 打不开按 3 秒退避重试，权限被拒绝时停止自动重试并给引导文案
  let micDesired = true
  let micTrying = false
  // 开麦代次：离开页面时递增，作废仍在等待授权的那次开麦
  let micEpoch = 0
  // 其他页面接管麦克风后置真：不再自动抢回，等本页重新挂载（attach）再取回
  let micYielded = false
  let nextMicAt = 0
  let micErrorNoted = ''

  let lastProbeAt = 0
  let waitedForEngine = false
  let lastActivityAt = 0
  let lastTickAt = 0
  // 是否因为 SPA 离开首页而挂起过：回到本页才需要重新 configure 取回麦克风
  let wasSuspended = false

  const listeners = new Set<() => void>()
  const emit = () => listeners.forEach((listener) => listener())
  const set = (patch: Partial<GuideState>) => {
    state = { ...state, ...patch }
    emit()
  }
  const apply = (event: GuideEvent) => {
    lastActivityAt = lastTickAt || lastActivityAt
    if (event.type === 'mic_taken') {
      // 另一个页面（通常是新开的首页标签）接管了麦克风：本页立即停麦并退让，
      // 不自动抢回；等用户回到本页时由 attach() 重新 configure 取回。
      micOn = false
      micDesired = false
      micYielded = true
      transport.stopMic()
    }
    state = reduceGuide(state, event)
    emit()
  }
  const note = (text: string, error = false) => {
    state = pushGuideHeard(state, 'system', text, error)
    emit()
  }

  /** 每次回到首页都重新 configure：引擎按"最新 configure 的页面持有麦克风"仲裁，
      离开期间可能被别的页面（新开标签）接管过。 */
  const sendConfigure = () => {
    transport.send({
      type: 'configure',
      mode: 'default',
      persona: 'guide',
      wake_word: '你好',
      page_token: options.pageToken,
    })
  }

  const connect = () => {
    if (connected || connecting || disposed) return
    connecting = (async () => {
      try {
        const ready = await transport.ready().catch(() => false)
        if (!ready) {
          waitedForEngine = true
          set({ connectionDetail: GUIDE_TEXTS.engineLoading })
          return
        }
        transport.openEvents(
          (event) => {
            apply(event)
            if (event.type === 'module_jump' && event.module) options.onModuleJump(event.module)
          },
          () => {
            connected = false
            set({ connection: 'failed', connectionDetail: '对话通道已断开' })
          },
        )
        connected = true
        sendConfigure()
        set({ connection: 'connected', connectionDetail: '暖暖已连接' })
        // 连上就自动开麦（下一拍 tick 执行；此时 socket 可能还在 CONNECTING，
        // 传输层会把消息排队，open 后补发）
        nextMicAt = 0
        if (waitedForEngine) {
          waitedForEngine = false
          state = pushGuideHeard(state, 'system', GUIDE_TEXTS.engineReady)
          emit()
        }
      } catch (error) {
        note(`导览通道建立失败：${(error as Error)?.message ?? error}`, true)
      } finally {
        connecting = null
      }
    })()
  }

  /** SPA 回到首页：WS/引擎会话仍活着时只恢复本页状态，不重连、不重载。 */
  const attach = () => {
    transport.suppressPlayback?.(false)
    if (disposed || connecting) return
    // 回到本页从"现在"重新计空闲：离开的这段时间不算 3 分钟自动刷新
    lastActivityAt = performance.now()
    micYielded = false
    micDesired = true
    nextMicAt = 0
    if (connected) {
      if (wasSuspended) sendConfigure()
      wasSuspended = false
      set({ connection: 'connected', connectionDetail: '暖暖已连接，说「你好」开始' })
      return
    }
    wasSuspended = false
    lastProbeAt = 0
    connect()
  }

  /** SPA 离开首页：停麦 + 停语音，WS 与引擎侧会话记忆保留（旧 Vue 版同口径）。 */
  const suspend = () => {
    if (disposed) return
    wasSuspended = true
    micEpoch += 1
    micOn = false
    micTrying = false
    micDesired = true
    transport.stopMic()
    // 先抑制再冲队列：之后到达的 PCM 直接丢弃，不会被别的页面听到
    transport.suppressPlayback?.(true)
    transport.stopPlayback()
    set({ listening: false, speaking: false, busy: false })
  }

  /**
   * 开麦：auto=true 是常听自动开（不显式开闸，靠唤醒词），
   * auto=false 是用户手动点麦克风（明确对话意图，补 awake 免唤醒）。
   */
  const startMic = async (auto: boolean) => {
    if (listeningMic() || micTrying || disposed || micYielded) return
    const epoch = micEpoch
    micTrying = true
    try {
      await transport.startMic((buffer) => transport.sendPcm(buffer))
      // 授权期间页面已离开（suspend 递增 epoch）：这次开麦结果作废并释放设备
      if (disposed || epoch !== micEpoch) {
        transport.stopMic()
        return
      }
      micOn = true
      micErrorNoted = ''
      if (!auto) transport.send({ type: 'awake' })
      set({
        listening: true,
        connectionDetail: auto ? '聆听中，说「你好」开始对话' : '麦克风已开启，说完停顿一下自动识别',
      })
    } catch (error) {
      // 已离开页面：这次开麦失败不再进错误日志（恢复后由 attach 重新尝试）
      if (disposed || epoch !== micEpoch) return
      const name = (error as Error)?.name ?? ''
      let why: string = GUIDE_TEXTS.micGeneric
      if (name === 'NotAllowedError' || name === 'SecurityError') why = GUIDE_TEXTS.micDenied
      else if (name === 'NotReadableError' || name === 'TrackStartError') why = GUIDE_TEXTS.micBusy
      else if (name === 'NotFoundError' || name === 'DevicesNotFoundError' || name === 'OverconstrainedError') why = GUIDE_TEXTS.micMissing
      else if (name === 'TimeoutError') why = GUIDE_TEXTS.micNoPrompt
      micOn = false
      // 权限被拒/无设备：自动重试没有意义，交给手动按钮；其余（设备忙、策略挂起）
      // 3 秒后重试，直到开成——用户口径是"麦克风常开"
      if (name === 'NotAllowedError' || name === 'SecurityError'
        || name === 'NotFoundError' || name === 'DevicesNotFoundError'
        || name === 'TimeoutError') {
        // 权限被拒/无设备/浏览器静默拒绝：自动重试没有意义，交给手动按钮
        micDesired = false
      }
      nextMicAt = (lastTickAt || 0) + 3000
      if (why !== micErrorNoted) {
        micErrorNoted = why
        state = pushGuideHeard({ ...state, listening: false }, 'system', why, true)
        emit()
      }
    } finally {
      micTrying = false
    }
  }
  const listeningMic = () => micOn || state.listening

  const tick = (now: number) => {
    if (disposed) return
    lastTickAt = now
    // 空闲自动刷新：聊过天且 3 分钟无任何引擎事件
    if (
      connected &&
      state.heard.length > 0 &&
      lastActivityAt > 0 &&
      now - lastActivityAt >= GUIDE_IDLE_RESET_MS
    ) {
      lastActivityAt = now
      transport.send({ type: 'reset', keep_awake: true })
    }
    // 未连接：每 3 秒重试（内部先查就绪）
    if (!connected && !connecting && now - lastProbeAt >= 3000) {
      lastProbeAt = now
      connect()
    }
    // 麦克风常开：连上后自动开麦；打不开按退避重试（tick 驱动，不用定时器）
    // micYielded：被其他页面接管时不抢回，等 attach（用户回到本页）再取回
    if (connected && micDesired && !micYielded && !micTrying && !micOn && now >= nextMicAt) {
      nextMicAt = now + 3000
      void startMic(true)
    }
  }

  return {
    subscribe: (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    getSnapshot: () => state,
    connect,
    attach,
    suspend,
    interrupt: () => {
      if (disposed) return
      transport.send({ type: 'cancel' })
      transport.stopPlayback()
      state = { ...state, busy: false, speaking: false }
      emit()
    },
    setPlayback: (active) => {
      if (disposed) return
      state = applyGuideAction(state, { type: 'playback', active })
      emit()
    },
    tick,
    sendText: (text) => {
      const content = (text || '').trim()
      if (!content || !connected) return
      transport.send({ type: 'cancel' })
      transport.send({ type: 'text', text: content })
    },
    toggleMic: async () => {
      if (micOn) {
        micOn = false
        micDesired = false // 用户主动关麦：不再自动重开
        transport.stopMic()
        transport.send({ type: 'cancel' })
        set({ listening: false, connectionDetail: '麦克风已关闭' })
        return
      }
      // 用户明确要开麦：解除让位状态，重新拿回
      micYielded = false
      micDesired = true
      await startMic(false)
    },
    resetSession: () => transport.send({ type: 'reset', keep_awake: true }),
    dispose: () => {
      disposed = true
      micOn = false
      micDesired = false
      transport.closeAll()
    },
  }
}
