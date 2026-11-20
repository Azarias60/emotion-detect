/**
 * 数字人陪伴会话（application 层）：只做编排与状态，不含任何平台 API。
 *
 * 平台能力（fetch / WebRTC / WebSocket / 麦克风 / 定时器）全部由上层注入：
 * - 传输端口 `DigitalHumanTransport` 定义在 domain、实现在 api/digitalHuman.ts；
 * - 轮询节奏由 app/ 层调用 `tick(now)` 驱动（与 useDetectionSession 同范式）。
 * 这样本文件可在 node 下用假传输直接单测（tests/digital-human.test.mjs）。
 */
import {
  TEXTS,
  initialDigitalHumanState,
  pushMessage,
  reduceDigitalHuman,
  type DigitalHumanState,
  type DigitalHumanTransport,
  type EngineAssets,
  type EngineEvent,
  type MessageRetry,
} from '../domain/companionLive'

/** 说话检测的轮询间隔（ms）：与页面 tick 频率一致，500ms 足够捕边沿 */
export const SPEAKING_POLL_MS = 500
/** 一轮说话最长跟踪时间：超过就复位，避免轮询永不停止 */
const SPEAKING_MAX_MS = 120000
/** 空闲自动刷新：3 分钟没有任何对话活动就请引擎软重置并清空对话 */
export const IDLE_RESET_MS = 3 * 60 * 1000
/** 当前播放片段轮询间隔（引擎状态机，与原版 800ms 同节奏） */
export const SEGMENT_POLL_MS = 800
/** 素材生成轮询间隔：有 running 任务时每 3 秒拉一次素材库；空闲时每 15 秒静默刷新 */
export const ASSET_POLL_MS = 3000
export const ASSET_IDLE_POLL_MS = 15000
/** 字幕条"刚说完"窗口：停止 10 秒后回引导语 */
export const CAPTION_FRESH_MS = 10000
/** 麦克风静音判定：开麦后 5 秒音量始终为 0 → 提示检查权限/设备 */
export const MIC_SILENT_MS = 5000

export interface DigitalHumanSession {
  subscribe: (listener: () => void) => () => void
  getSnapshot: () => DigitalHumanState
  /** 页面挂载/回到本页：把（可能仍活着的）实时通道接到新的 video 元素上 */
  attach: (video: HTMLVideoElement) => void
  /** SPA 离开本页：停麦、摘掉视频，但保留 WebRTC/WS 会话（短时间回来秒恢复） */
  suspend: () => void
  /** 由页面按固定节奏驱动（说话检测、片段轮询、素材轮询、自动开播） */
  tick: (now: number) => void
  sendText: (text: string) => void
  toggleMic: () => Promise<void>
  /** 打断当前播报（双保险：WS cancel + /interrupt_talk） */
  interrupt: () => void
  /** 连接失败后的手动重连（保持当前形象） */
  retry: () => Promise<void>
  /** 请求引擎软重置（3 分钟空闲刷新 / 重新开始） */
  resetSession: (keepAwake: boolean) => void
  /** 刷新素材库（数字人/音色/待生成列表） */
  refreshAssets: () => Promise<void>
  /** 选择"数字人"预设：一项 = 形象 + 音色一对 */
  selectPreset: (id: string) => Promise<void>
  /** 手动切换音色（唯一允许打破配对的入口） */
  switchVoice: (id: string) => Promise<void>
  /** drop 视频一键生成新形象 */
  generateAvatar: (name: string) => Promise<void>
  /** 删除数字人（删除当前形象会自动切回默认并重连） */
  deleteAvatar: (id: string, name: string) => Promise<boolean>
  /** 跳一段舞（当前形象段表带 dance 段时可用） */
  dance: () => Promise<void>
  /** 浏览器拦了自动播放时的"点击开播" */
  tapPlay: () => void
  /** 收起推荐/舞蹈邀请 */
  clearRecommendation: () => void
  /** 往聊天流插一条不打断的灰字（制作完成/音色绑定等编排结果用） */
  note: (content: string) => void
  dispose: () => void
}

export function createDigitalHumanSession(transport: DigitalHumanTransport): DigitalHumanSession {
  let state = initialDigitalHumanState
  let disposed = false
  let videoEl: HTMLVideoElement | null = null
  /** 当前 WebRTC 媒体流：离开页面后 video 元素被卸载，流本身留在会话里，回来重新挂上 */
  let videoStream: HTMLVideoElement['srcObject'] = null
  let sessionId = ''
  let connecting: Promise<void> | null = null
  let connected = false
  /** 当前选择的形象 id（/offer 时带给服务端） */
  let avatarId = ''
  /** 切换数字人中：重建 WebRTC 期间视频是黑的，页面给"切换中"遮罩 */
  let switching = false

  // 麦克风常开（与原 Vue 版同口径）：进页面连上就自动开麦，唤醒门保持待命；
  // 打不开按 3 秒退避重试，权限被拒则停止自动重试
  let micDesired = true
  let micTrying = false
  // 开麦代次：离开页面时递增，作废仍在等待授权的那次开麦
  let micEpoch = 0
  let nextMicAt = 0
  let micErrorNoted = ''
  let micOn = false
  // 唤醒闸状态：手动点麦克风 = 明确对话意图（补 awake 免唤醒）；
  // 语音喊「你好」唤醒过的（引擎回 wake）在重连补发 configure 后也要补 awake，
  // 否则引擎 configure 分支会无条件重置唤醒门，"喊过你好之后又没反应"
  let manualAwake = false
  let everWoke = false

  // 用户手动指定过的音色（唯一允许破坏"人物↔音色"配对的途径）
  let manualVoice = ''

  // 模型就绪探测：引擎首次启动约需半分钟
  let lastProbeAt = 0
  let waitedForEngine = false

  // 说话检测
  let speakingPollAt = 0
  let speakingElapsed = 0
  let everSpoke = false
  let watchingSpeaking = false
  // 最近一次对话活动的时刻（tick 的时钟）：空闲刷新据此判断"3 分钟没动静"
  let lastActivityAt = 0
  let lastTickAt = 0

  // 片段 / 素材轮询节奏
  let lastSegmentAt = 0
  let lastAssetPollAt = 0

  // 字幕条"刚说完"窗口与静音诊断
  let lastReplyAt = 0
  let lastNonSilentAt = 0

  // 自动开播重试（浏览器自动播放策略）
  let nextPlayAt = 0
  let playTries = 0

  const listeners = new Set<() => void>()
  const emit = () => listeners.forEach((listener) => listener())
  const set = (patch: Partial<DigitalHumanState>) => {
    state = { ...state, ...patch }
    emit()
  }
  const patchAssets = (patch: Partial<EngineAssets>) => {
    if (!state.assets) return
    set({ assets: { ...state.assets, ...patch } })
  }
  const note = (content: string) => {
    state = pushMessage(state, 'action', content)
    emit()
  }
  const fail = (message: string, retry: MessageRetry = 'conn') => {
    state = pushMessage({ ...state, busy: false, speaking: false, pipeline: 0 }, 'error', message, retry)
    emit()
  }
  const apply = (event: EngineEvent) => {
    // 任何引擎事件都算"对话活动"（转写/回复/提示/状态），空闲刷新据此计时
    lastActivityAt = lastTickAt || lastActivityAt
    if (event.type === 'assistant_delta') lastReplyAt = performance.now()
    state = reduceDigitalHuman(state, event)
    // 事件后的编排副作用（不改事件本身的语义）
    if (event.type === 'transcript') startSpeakingWatch()
    if (event.type === 'wake') everWoke = true
    if (event.type === 'session_reset') {
      stopSpeakingWatch()
      state = { ...state, segment: null }
    }
    // 陪伴页的角色分工：kind=module 的推荐卡只在导览页弹（2026-10-26 用户拍板），
    // 这里只保留聊天流里的"建议：…"灰字；kind=dance 的舞蹈邀请保留成可点的一行
    if (event.type === 'recommend' && (event.kind ?? 'module') !== 'dance') {
      state = { ...state, recommendation: null }
    }
    if (event.type === 'configured') {
      if (micOn && (manualAwake || everWoke)) transport.send({ type: 'awake' })
      void syncPersona()
    }
    emit()
  }

  const startSpeakingWatch = () => {
    watchingSpeaking = true
    speakingElapsed = 0
    speakingPollAt = 0
    everSpoke = false
  }

  const stopSpeakingWatch = () => {
    watchingSpeaking = false
    everSpoke = false
  }

  const pollSpeaking = async () => {
    speakingElapsed += SPEAKING_POLL_MS
    if (!sessionId || speakingElapsed > SPEAKING_MAX_MS) {
      stopSpeakingWatch()
      set({ speaking: false, busy: false, pipeline: 0 })
      return
    }
    const speaking = await transport.isSpeaking(sessionId).catch(() => false)
    if (speaking) {
      everSpoke = true
      set({ speaking: true, busy: false, pipeline: 4, connectionDetail: '数字人正在说话…' })
    } else if (everSpoke) {
      stopSpeakingWatch()
      set({ speaking: false, busy: false, pipeline: 0, connectionDetail: '待命中，说说话或输入文字都可以' })
    }
  }

  // ---------- 素材库 / 人设 / 音色配对 ----------

  const loadAssets = async () => {
    const data = await transport.assets()
    if (disposed) return
    if (!data) return
    if (!avatarId && data.defaultAvatar) avatarId = data.defaultAvatar
    const unchanged = state.currentAvatar === avatarId
      && state.assets !== null
      && JSON.stringify(state.assets) === JSON.stringify(data)
    if (!unchanged) {
      set({ assets: data, currentAvatar: avatarId })
    }
    void syncVoiceToAvatar()
  }

  /** 人物→音色强绑定：形象与音色不成对时自动拨回配对值（手动指定过则尊重手动）。 */
  const syncVoiceToAvatar = async () => {
    const assets = state.assets
    const avatar = avatarId || assets?.defaultAvatar || ''
    if (!assets || !avatar) return
    const pair = assets.presets.find((p) => p.avatar === avatar && p.ready)
    if (!pair) return
    if (manualVoice && manualVoice === assets.activeVoice) return
    if (assets.activeVoice === pair.voice) {
      manualVoice = ''
      return
    }
    try {
      const voice = await transport.setVoice(pair.voice)
      if (disposed) return
      patchAssets({ activeVoice: voice ?? pair.voice })
      manualVoice = ''
      note(TEXTS.voicePaired(pair.voice))
    } catch {
      /* 引擎不在时静默：下次刷新素材库再试 */
    }
  }

  /** 陪伴页只认「数字关怀助手」人设：引擎全局档停在别的档时进页自动切回。 */
  const syncPersona = async () => {
    const data = await transport.personas().catch(() => null)
    if (disposed || !data?.active || !data.personas?.length) return
    const care = data.personas.find((p) => p.id === 'care')
      || data.personas.find((p) => p.name === '数字关怀助手')
    if (care && data.active !== care.id) {
      await transport.setPersona(care.id).catch(() => undefined)
    }
  }

  /** 断开当前画面/事件通道并用指定形象重连（换"数字人"时用）。 */
  const reconnect = async (nextAvatar: string, name: string) => {
    // 不要把状态设成 connecting——ensureConnected 的守卫会把自己拦回去；
    // 用 switching 标记，页面据此显示"正在切换到 X"遮罩
    switching = true
    set({ switching: true, connectionDetail: `正在切换到 ${name || nextAvatar}，重建实时画面…` })
    transport.closeMedia()
    connected = false
    sessionId = ''
    videoStream = null
    try {
      await ensureConnected()
    } finally {
      switching = false
      if (state.switching) set({ switching: false })
    }
  }

  /** 建连唯一入口：先确认引擎就绪，未就绪就挂起等 tick 重试。 */
  const ensureConnected = async () => {
    if (connected || connecting) return connecting ?? undefined
    connecting = (async () => {
      const video = videoEl
      if (!video) {
        connecting = null
        return
      }
      try {
        const ready = await transport.ready().catch(() => false)
        if (!ready) {
          // 引擎首次启动要加载模型（约半分钟）：这期间不建连，页面上显示加载中文案
          waitedForEngine = true
          set({ connection: 'idle', connectionDetail: TEXTS.engineLoading })
          return
        }
        set({
          connection: 'connecting',
          connectionDetail: state.connectionDetail || '正在建立实时通道…',
        })
        // 重连前先清掉可能残留的旧 peer/事件通道（WS 掉线但画面还在的情况），
        // 否则旧 pc 会在引擎侧留下一条无人观看的渲染会话
        transport.closeMedia()
        sessionId = await transport.openRtc(video, avatarId || undefined)
        // 记住媒体流：离开页面时 video 元素会卸载，流要留到回来重新挂上
        videoStream = video.srcObject
        transport.openEvents(
          sessionId,
          apply,
          () => {
            connected = false
            sessionId = ''
            stopSpeakingWatch()
            // 麦克风常开：掉线自动重连（tick 的 3 秒重试会把通道再拉起来），
            // 不把 UI 打成 failed——否则恢复成功后还得手点重试
            set({
              connection: micDesired ? 'connecting' : 'connected',
              connectionDetail: micDesired ? '连接波动，自动重连中…' : '对话通道已断开（麦克风未开），点麦克风按钮恢复',
            })
          },
        )
        connected = true
        transport.send({ type: 'configure', mode: 'live', sessionid: sessionId })
        set({ connection: 'connected', connectionDetail: '实时数字人已连接' })
        nextMicAt = 0
        if (waitedForEngine) {
          // 等过冷启动：给一条明确的"就绪了"，别让用户以为一直没反应
          waitedForEngine = false
          state = pushMessage(state, 'action', TEXTS.engineReady)
        }
        void loadAssets()
        void syncPersona()
        emit()
      } catch (error) {
        fail(`实时通道建立失败：${(error as Error)?.message ?? error}`)
      } finally {
        // 必须无条件清空：否则未就绪那一路会让后续 tick 永远不再重试
        connecting = null
      }
    })()
    return connecting
  }

  /**
   * 页面挂载/回到本页：同一个 video 元素的重复 ref 回调直接忽略；
   * 已连着的老会话把媒体流接回新元素并重新 configure（引擎按最新页面
   * configure 归属），不重建 WebRTC、不重新 offer。
   */
  const attach = (video: HTMLVideoElement) => {
    if (disposed || videoEl === video) return
    videoEl = video
    // 回到本页从"现在"重新计空闲：离开的这段时间不算 3 分钟自动刷新
    lastActivityAt = performance.now()
    if (videoStream) {
      video.srcObject = videoStream
      void video.play().catch(() => undefined)
    }
    if (connected) {
      transport.send({ type: 'configure', mode: 'live', sessionid: sessionId })
      nextMicAt = 0
      set({ connection: 'connected', connectionDetail: '实时数字人已连接' })
      void loadAssets()
      return
    }
    lastProbeAt = 0
    void ensureConnected()
  }

  /** SPA 离开本页：停麦 + 摘视频，WebRTC/WS 会话保留（由上层租约决定何时真正销毁）。 */
  const suspend = () => {
    if (disposed) return
    micEpoch += 1
    stopSpeakingWatch()
    micOn = false
    micTrying = false
    micDesired = true
    transport.stopMic()
    if (videoEl) {
      if (videoEl.srcObject) videoStream = videoEl.srcObject
      try {
        videoEl.pause()
      } catch { /* 元素可能已在卸载流程里 */ }
      videoEl.srcObject = null
      videoEl = null
    }
    set({ listening: false, speaking: false, busy: false, pipeline: 0, needPlayTap: false })
  }

  // ---------- 诊断（音量 / 上行 / 引擎回传）----------

  transport.onDiagnostics((diag) => {
    if (disposed) return
    const prev = state.diag
    if (diag.micLevel > 0.02) {
      lastNonSilentAt = lastTickAt
      if (state.micSilent) set({ micSilent: false })
    }
    if (
      prev.micLevel !== diag.micLevel
      || prev.uplinkBytes !== diag.uplinkBytes
      || prev.engineEvents !== diag.engineEvents
      || prev.audioBlocked !== diag.audioBlocked
    ) {
      set({ diag })
    }
  })

  const tick = (now: number) => {
    if (disposed) return
    lastTickAt = now
    // 0) 空闲自动刷新：3 分钟没有任何对话活动且聊过天 → 请引擎软重置
    //    keep_awake：麦克风常开，刷新后直接开口就有回应，不用再喊"你好"
    if (
      connected &&
      state.messages.length > 0 &&
      lastActivityAt > 0 &&
      now - lastActivityAt >= IDLE_RESET_MS
    ) {
      lastActivityAt = now
      transport.send({ type: 'reset', keep_awake: true })
    }
    // 1) 未连接：每 3 秒重试一次建连（ensureConnected 内部先查就绪）
    if (!connected && videoEl && !connecting && now - lastProbeAt >= 3000) {
      lastProbeAt = now
      void ensureConnected()
    }
    // 2) 正在说：按轮询间隔取一次引擎状态
    if (watchingSpeaking && now - speakingPollAt >= SPEAKING_POLL_MS) {
      speakingPollAt = now
      void pollSpeaking()
    }
    // 3) 麦克风常开：连上后自动开麦；打不开按退避重试
    if (connected && micDesired && !micOn && !micTrying && now >= nextMicAt) {
      nextMicAt = now + 3000
      void openMic(true)
    }
    // 4) 当前播放片段（待机框展示播放逻辑）
    if (connected && sessionId && now - lastSegmentAt >= SEGMENT_POLL_MS) {
      lastSegmentAt = now
      void transport.segment(sessionId).then((segment) => {
        if (disposed || !segment) return
        if (segment.segment !== state.segment?.segment || segment.kind !== state.segment?.kind) {
          set({ segment })
        }
      })
    }
    // 5) 素材库后台静默刷新：有 running 任务时每 3 秒拉一次，空闲时每 15 秒拉一次，无需用户手动点刷新
    const hasRunning = state.assets?.generations.some((job) => job.status === 'running') ?? false
    const assetPollInterval = hasRunning ? ASSET_POLL_MS : ASSET_IDLE_POLL_MS
    if (connected && now - lastAssetPollAt >= assetPollInterval) {
      lastAssetPollAt = now
      void loadAssets()
    }
    // 6) 字幕条"刚说完"窗口
    const fresh = lastReplyAt > 0 && now - lastReplyAt < CAPTION_FRESH_MS
    if (state.captionFresh !== fresh) set({ captionFresh: fresh })
    // 7) 麦克风静音诊断：开着麦但 5 秒没有输入
    const silent = state.listening && lastNonSilentAt > 0 && now - lastNonSilentAt >= MIC_SILENT_MS
    if (state.micSilent !== silent) set({ micSilent: silent })
    // 8) 自动开播：浏览器拦截 play() 时定期重试，仍失败给"点击开播"
    if (connected && videoEl) {
      if (!videoEl.paused) {
        playTries = 0
        if (state.needPlayTap) set({ needPlayTap: false })
      } else if (now >= nextPlayAt) {
        nextPlayAt = now + 2500
        playTries += 1
        void videoEl.play()
          .then(() => {
            if (state.needPlayTap) set({ needPlayTap: false })
          })
          .catch(() => {
            if (!state.needPlayTap && playTries >= 2) set({ needPlayTap: true })
          })
      }
    }
  }

  const sendText = (text: string) => {
    const content = (text || '').trim()
    if (!content || !connected) return
    // 文字输入也是一轮新对话：先打断在播回复，避免新旧语音叠着念
    transport.send({ type: 'cancel' })
    transport.send({ type: 'text', text: content })
    startSpeakingWatch()
  }

  const openMic = async (auto = false) => {
    if (micOn || micTrying || disposed) return
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
      lastNonSilentAt = lastTickAt
      // 自动开麦走唤醒门（说「你好」）；手动点麦克风是明确对话意图，补 awake 免唤醒
      manualAwake = !auto
      if (!auto) transport.send({ type: 'awake' })
      set({
        listening: true,
        connectionDetail: auto ? TEXTS.micOpenWithWake : TEXTS.micOpenAwake,
      })
    } catch (error) {
      // 已离开页面：这次开麦失败不再进错误日志（回来由 tick 重新尝试）
      if (disposed || epoch !== micEpoch) return
      // 按错误类型给"下一步动作"文案；麦克风问题不影响已建立的实时通道
      const name = (error as Error)?.name ?? ''
      let why: string = TEXTS.micGeneric
      if (name === 'NotAllowedError' || name === 'SecurityError') why = TEXTS.micDenied
      else if (name === 'NotReadableError' || name === 'TrackStartError') why = TEXTS.micBusy
      else if (name === 'NotFoundError' || name === 'DevicesNotFoundError' || name === 'OverconstrainedError') why = TEXTS.micMissing
      else if (name === 'TimeoutError') why = TEXTS.micNoPrompt
      micOn = false
      manualAwake = false
      if (name === 'NotAllowedError' || name === 'SecurityError'
        || name === 'NotFoundError' || name === 'DevicesNotFoundError'
        || name === 'TimeoutError') {
        micDesired = false
      }
      nextMicAt = (lastTickAt || 0) + 3000
      if (why !== micErrorNoted) {
        micErrorNoted = why
        state = pushMessage({ ...state, listening: false }, 'error', why, 'mic')
        emit()
      }
    } finally {
      micTrying = false
    }
  }

  const closeMic = () => {
    micOn = false
    micDesired = false // 用户主动关麦：不再自动重开
    manualAwake = false
    transport.stopMic()
    transport.send({ type: 'cancel' })
    set({ listening: false, connectionDetail: TEXTS.micClosed })
  }

  const interrupt = () => {
    const wasSpeaking = state.speaking || state.busy
    transport.send({ type: 'cancel' })
    // 双保险：WS cancel 已能让服务端停手，再打 /interrupt_talk 确保 WebRTC 播报立即停
    if (sessionId && connected) transport.interruptTalk(sessionId)
    stopSpeakingWatch()
    set({
      busy: false,
      speaking: false,
      pipeline: 0,
      bargeInPending: false,
      connectionDetail: wasSpeaking ? '已打断，我在听' : state.connectionDetail,
    })
  }

  const retry = async () => {
    if (connecting) return
    transport.closeMedia()
    connected = false
    sessionId = ''
    videoStream = null
    stopSpeakingWatch()
    set({ connectionDetail: '正在重新建立实时通道…' })
    await ensureConnected()
  }

  const selectPreset = async (id: string) => {
    const preset = state.assets?.presets.find((p) => p.id === id)
    // busy 不拦截：切换是用户明确意图（下面先打断在播回复）
    if (!preset || !preset.ready) return
    if (state.busy || state.speaking) interrupt()
    const current = avatarId || state.assets?.defaultAvatar || ''
    const avatarChanged = preset.avatar !== current
    const voiceChanged = preset.voice !== state.assets?.activeVoice
    if (!avatarChanged && !voiceChanged) return
    // 选人物=选一对：音色强制跟到该人物的绑定音色（手动覆盖记录清零）
    manualVoice = ''
    if (avatarChanged) {
      avatarId = preset.avatar
      set({ currentAvatar: preset.avatar })
      await reconnect(preset.avatar, preset.name)
    }
    if (voiceChanged) {
      try {
        const voice = await transport.setVoice(preset.voice)
        patchAssets({ activeVoice: voice ?? preset.voice })
      } catch (error) {
        note(`音色切换失败：${(error as Error)?.message ?? error}`)
      }
    }
    await loadAssets()
  }

  const switchVoice = async (id: string) => {
    if (!id) return
    try {
      const voice = await transport.setVoice(id)
      patchAssets({ activeVoice: voice ?? id })
      manualVoice = id
      const name = state.assets?.voices.find((v) => v.id === id)?.name || id
      note(TEXTS.voiceManual(name))
      // 面板保持展开：切完要让用户看到"使用中"的确认反馈
    } catch (error) {
      note(`音色切换失败：${(error as Error)?.message ?? error}`)
    }
  }

  const generateAvatar = async (name: string) => {
    try {
      const message = await transport.generateAvatar(name)
      note(message || `已开始生成人物 ${name}，完成后点「数字人」即可看到`)
    } catch (error) {
      note(`生成失败：${(error as Error)?.message ?? error}`)
    }
    await loadAssets()
  }

  const deleteAvatar = async (id: string, name: string) => {
    try {
      const message = await transport.deleteAvatar(id)
      note(message || `已删除人物 ${name || id}`)
      // 删的是当前形象 → 立刻切回默认形象重连，别停在已删除的素材上
      if (avatarId === id) {
        const data = await transport.assets().catch(() => null)
        const fallback = data?.presets.find((p) => p.avatar === data?.defaultAvatar)
        avatarId = data?.defaultAvatar || ''
        set({ currentAvatar: avatarId })
        await reconnect(avatarId, fallback?.name || '默认形象')
      }
      await loadAssets()
      return true
    } catch (error) {
      note(`删除失败：${(error as Error)?.message ?? error}`)
      return false
    }
  }

  const dance = async () => {
    if (!sessionId || !connected || state.dancing) return
    set({ dancing: true })
    try {
      const result = await transport.dance(sessionId)
      if (!result.ok && result.message) note(result.message)
    } catch (error) {
      note(`舞蹈启动失败：${(error as Error)?.message ?? error}`)
    } finally {
      set({ dancing: false })
    }
  }

  const tapPlay = () => {
    if (!videoEl) return
    void videoEl.play()
      .then(() => set({ needPlayTap: false }))
      .catch(() => undefined)
  }

  return {
    subscribe: (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    getSnapshot: () => state,
    attach,
    suspend,
    tick,
    sendText,
    toggleMic: async () => {
      if (micOn) closeMic()
      else {
        micDesired = true
        await openMic(false)
      }
    },
    interrupt,
    retry,
    resetSession: (keepAwake: boolean) => transport.send({ type: 'reset', keep_awake: keepAwake }),
    refreshAssets: loadAssets,
    selectPreset,
    switchVoice,
    generateAvatar,
    deleteAvatar,
    dance,
    tapPlay,
    clearRecommendation: () => {
      if (state.recommendation) set({ recommendation: null })
    },
    note: (content: string) => {
      // 与引擎的 action 行同款样式：同文案不重复刷屏
      const last = state.messages[state.messages.length - 1]
      if (last && last.role === 'action' && last.content === content) return
      note(content)
    },
    dispose: () => {
      disposed = true
      stopSpeakingWatch()
      micOn = false
      micDesired = false
      transport.closeAll()
      if (videoEl) videoEl.srcObject = null
      videoEl = null
      videoStream = null
    },
  }
}
