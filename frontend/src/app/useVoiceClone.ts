import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react'
import { createVoiceCloneSession, type VoiceCloneSession } from '../application/voiceClone'
import type { ClonePort, VoiceCloneState } from '../domain/voiceClone'

const TICK_MS = 500
const DISPOSE_GRACE_MS = 300

export interface VoiceCloneBinding {
  state: VoiceCloneState
  loadSentences: () => void
  loadOfficial: () => void
  pickSentence: () => void
  start: () => void
  /** 停录并上传；avatarId 非空立即绑定，为空挂起等制作完成后自动绑 */
  stopAndUpload: (avatarId?: string) => Promise<string>
  chooseOfficial: (voiceId: string, avatarId?: string) => Promise<boolean>
  bindTo: (avatarId: string) => Promise<boolean>
}

/** 语音克隆会话的 React 绑定：录音计时/15 秒自动停由本 hook 的 tick 驱动。 */
export function useVoiceClone(port: ClonePort): VoiceCloneBinding {
  const session: VoiceCloneSession = useMemo(() => createVoiceCloneSession(port), [port])
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot)
  const disposeTimer = useRef<number | null>(null)

  useEffect(() => {
    if (disposeTimer.current !== null) {
      clearTimeout(disposeTimer.current)
      disposeTimer.current = null
    }
    const timer = setInterval(() => session.tick(performance.now()), TICK_MS)
    return () => {
      clearInterval(timer)
      // 卸载必须释放麦克风：浏览器采集指示灯不能亮到页面外（隐私事故）
      disposeTimer.current = window.setTimeout(() => {
        session.dispose()
        disposeTimer.current = null
      }, DISPOSE_GRACE_MS)
    }
  }, [session])

  return {
    state,
    loadSentences: () => void session.loadSentences(),
    loadOfficial: () => void session.loadOfficial(),
    pickSentence: session.pickSentence,
    start: () => void session.start(),
    stopAndUpload: (avatarId) => session.stopAndUpload(avatarId),
    chooseOfficial: (voiceId, avatarId) => session.chooseOfficial(voiceId, avatarId),
    bindTo: (avatarId) => session.bindTo(avatarId),
  }
}
