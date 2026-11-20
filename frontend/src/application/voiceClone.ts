/**
 * 语音克隆会话（application 层）：引导句 → 录音 → 上传 → 绑定。
 * 录音采集与编码在 api/voiceClone.ts（浏览器 API 只在基础设施层）；
 * 计时/自动停（15 秒）由页面 tick 驱动，不在核心里起定时器。
 */
import {
  MAX_RECORD_SECONDS,
  initialVoiceCloneState,
  pickSentence,
  type ClonePort,
  type VoiceCloneState,
} from '../domain/voiceClone'

export interface VoiceCloneSession {
  subscribe: (listener: () => void) => () => void
  getSnapshot: () => VoiceCloneState
  /** 由页面按固定节奏驱动（录音计时） */
  tick: (now: number) => void
  loadSentences: () => Promise<void>
  loadOfficial: () => Promise<void>
  pickSentence: () => void
  /** 开始录音（失败置 error） */
  start: () => Promise<void>
  /** 停录并上传；avatarId 非空则立即绑定，为空则挂起等制作完成后自动绑 */
  stopAndUpload: (avatarId?: string) => Promise<string>
  /** 选官方备用音色：有目标数字人立即绑定，否则挂起 */
  chooseOfficial: (voiceId: string, avatarId?: string) => Promise<boolean>
  /** 绑定已上传的音色到指定数字人 */
  bindTo: (avatarId: string) => Promise<boolean>
  dispose: () => void
}

export function createVoiceCloneSession(port: ClonePort): VoiceCloneSession {
  let state = initialVoiceCloneState
  let disposed = false
  /** 录音起点（tick 时钟；0 = 还没取到第一个 tick） */
  let startedAt = 0
  let starting = false
  let stopping: Promise<string> | null = null
  let stopRequested = false // starting 期间点了停止：开麦成功后立即停

  const listeners = new Set<() => void>()
  const emit = () => listeners.forEach((listener) => listener())
  const set = (patch: Partial<VoiceCloneState>) => {
    state = { ...state, ...patch }
    emit()
  }

  const loadSentences = async () => {
    const sentences = await port.sentences().catch(() => [] as string[])
    if (disposed) return
    // 失败也重挑一句：否则引导句区域显示过期内容
    set({ sentences, sentence: pickSentence(sentences) || state.sentence })
  }

  const loadOfficial = async () => {
    const officialVoices = await port.officialVoices().catch(() => [])
    if (disposed) return
    set({ officialVoices })
  }

  const pickNextSentence = () => {
    set({ sentence: pickSentence(state.sentences.length ? state.sentences : ['']) })
  }

  const start = async () => {
    if (starting || state.recording || disposed) return
    starting = true
    stopRequested = false
    set({ error: '', done: '', officialVoice: '' })
    try {
      await port.startRecording()
      if (disposed || stopRequested) return
      startedAt = 0 // 第一个 tick 再取起点，与应用层时钟一致
      set({ recording: true, seconds: 0 })
    } catch (error) {
      set({ error: `无法开启麦克风：${(error as Error)?.message ?? error}` })
    } finally {
      starting = false
    }
  }

  const stopAndUpload = (avatarId = '') => {
    if (!state.recording) {
      // 开麦进行中（getUserMedia 还没返回）：记一笔，开完立刻停
      if (starting) stopRequested = true
      return Promise.resolve('')
    }
    if (stopping) return stopping
    stopping = (async () => {
      set({ recording: false })
      const captured = await port.stopRecording()
      set({ seconds: 0 })
      if (disposed) return ''
      if (!captured) {
        set({ error: '录音为空，请重试', done: '' })
        return ''
      }
      set({ uploading: true })
      try {
        const { voiceId, duration } = await port.upload(captured.wav)
        if (avatarId) {
          await port.bind(voiceId, avatarId)
          set({
            voiceId,
            boundAvatar: avatarId,
            uploading: false,
            done: `音色已上传并绑定到「${avatarId}」，在数字人列表选择它即可使用`,
          })
        } else {
          set({
            voiceId,
            uploading: false,
            done: `音色已上传（${duration ?? '?'} 秒），制作数字人完成后可绑定`,
          })
        }
        return voiceId
      } catch (error) {
        // 失败时清掉上一次的成功文案，避免新旧提示同屏
        set({ uploading: false, error: (error as Error)?.message || '上传失败', done: '' })
        return ''
      } finally {
        stopping = null
      }
    })()
    return stopping
  }

  const chooseOfficial = async (voiceId: string, avatarId = '') => {
    if (!voiceId) return false
    set({ officialVoice: voiceId })
    if (!avatarId) {
      const label = voiceId === 'official_male' ? '男声' : '女声'
      set({ done: `已选官方音色（${label}），制作完成后自动使用` })
      return true
    }
    set({ uploading: true })
    try {
      await port.bind(voiceId, avatarId)
      set({ uploading: false, boundAvatar: avatarId, done: `官方音色已绑定到「${avatarId}」` })
      return true
    } catch (error) {
      set({ uploading: false, error: (error as Error)?.message || '绑定失败' })
      return false
    }
  }

  const bindTo = async (avatarId: string) => {
    if (!state.voiceId) return false
    try {
      await port.bind(state.voiceId, avatarId)
      set({ boundAvatar: avatarId, done: `音色已绑定到「${avatarId}」` })
      return true
    } catch (error) {
      set({ error: (error as Error)?.message || '绑定失败' })
      return false
    }
  }

  const tick = (now: number) => {
    if (disposed || !state.recording) return
    if (!startedAt) {
      startedAt = now
      return
    }
    const seconds = Math.floor((now - startedAt) / 1000)
    if (seconds !== state.seconds) set({ seconds })
    // 到 15 秒自动停并上传
    if (seconds >= MAX_RECORD_SECONDS) void stopAndUpload()
  }

  return {
    subscribe: (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    getSnapshot: () => state,
    tick,
    loadSentences,
    loadOfficial,
    pickSentence: pickNextSentence,
    start,
    stopAndUpload,
    chooseOfficial,
    bindTo,
    dispose: () => {
      disposed = true
      // 录音中/开麦中离开页面：停轨道停上下文——浏览器麦克风指示灯不能一直亮着
      if (state.recording) set({ recording: false })
      void port.stopRecording().catch(() => undefined)
    },
  }
}
