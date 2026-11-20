import { useEffect, useMemo, useSyncExternalStore } from 'react'
import { createDigitalHumanSession, type DigitalHumanSession } from '../application/companionLive'
import type { DigitalHumanState, DigitalHumanTransport } from '../domain/companionLive'

/** 页面驱动会话的节奏：与 application 层的说话轮询间隔一致。 */
const TICK_MS = 500
/**
 * 离开数字人页后会话保留时长：这期间回到页面直接接回旧会话（秒开）；
 * 超时才真正销毁 WebRTC/WS，释放引擎渲染资源。数字人会话在引擎侧
 * 会持续渲染帧，不能像暖暖的 WS 一样永久保活。
 */
const DISPOSE_LEASE_MS = 5 * 60 * 1000

type CachedCompanionSession = {
  session: DigitalHumanSession
  /** 离开页面后的延迟销毁定时器（回到页面时取消） */
  lease: number | null
  pagehideBound: boolean
}

/** 每个 transport 一条会话：SPA 换页只 suspend，短时间内回来不再重建 WebRTC。 */
const sessions = new WeakMap<DigitalHumanTransport, CachedCompanionSession>()

function acquireCompanionSession(transport: DigitalHumanTransport): CachedCompanionSession {
  let entry = sessions.get(transport)
  if (!entry) {
    entry = { session: createDigitalHumanSession(transport), lease: null, pagehideBound: false }
    sessions.set(transport, entry)
  }
  if (!entry.pagehideBound) {
    entry.pagehideBound = true
    // 整页卸载（关标签/刷新）立即释放；bfcache 恢复（persisted）时保留
    window.addEventListener('pagehide', (event) => {
      if (event.persisted) return
      if (sessions.get(transport) !== entry) return
      if (entry.lease !== null) {
        clearTimeout(entry.lease)
        entry.lease = null
      }
      sessions.delete(transport)
      entry.session.dispose()
    })
  }
  return entry
}

export interface CompanionLiveBinding {
  state: DigitalHumanState
  connect: (video: HTMLVideoElement) => void
  sendText: (text: string) => void
  toggleMic: () => void
  /** 打断当前播报 */
  interrupt: () => void
  /** 连接失败后的手动重试（保持当前形象） */
  retry: () => void
  resetSession: () => void
  refreshAssets: () => void
  selectPreset: (id: string) => void
  switchVoice: (id: string) => void
  generateAvatar: (name: string) => void
  deleteAvatar: (id: string, name: string) => Promise<boolean>
  dance: () => void
  tapPlay: () => void
  clearRecommendation: () => void
  /** 往聊天流插一条灰字（制作完成/音色绑定等编排结果用） */
  note: (content: string) => void
}

/**
 * 数字人陪伴会话的 React 绑定（app 层）：接收组合根注入的传输端口，按节奏 tick，
 * 把快照与动作交给页面。页面与组件只接收这个绑定，不直接碰传输层。
 */
export function useCompanionLive(transport: DigitalHumanTransport): CompanionLiveBinding {
  const entry = useMemo(() => acquireCompanionSession(transport), [transport])
  const session = entry.session
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot)

  useEffect(() => {
    // 回到本页：取消离开时排队的销毁，复用同一会话
    if (entry.lease !== null) {
      clearTimeout(entry.lease)
      entry.lease = null
    }
    // 自动播放策略：无手势时采集上下文可能被挂起，首次点击/按键/切回标签页时恢复
    const resumeAudio = () => transport.resume?.()
    const onVisible = () => { if (!document.hidden) resumeAudio() }
    document.addEventListener('pointerdown', resumeAudio)
    document.addEventListener('keydown', resumeAudio)
    document.addEventListener('visibilitychange', onVisible)
    const timer = setInterval(() => session.tick(performance.now()), TICK_MS)
    return () => {
      clearInterval(timer)
      document.removeEventListener('pointerdown', resumeAudio)
      document.removeEventListener('keydown', resumeAudio)
      document.removeEventListener('visibilitychange', onVisible)
      // SPA 离开本页：停麦、摘视频，但保留 WebRTC/WS 会话；
      // 5 分钟内回来直接接回，超时再销毁释放引擎资源。
      session.suspend()
      entry.lease = window.setTimeout(() => {
        entry.lease = null
        if (sessions.get(transport) !== entry) return
        sessions.delete(transport)
        entry.session.dispose()
      }, DISPOSE_LEASE_MS)
    }
  }, [entry, session, transport])

  return {
    state,
    // 同一个 video 元素的重复 ref 回调由会话内部去重；换元素/回到本页时接管
    connect: (video) => session.attach(video),
    sendText: session.sendText,
    toggleMic: () => void session.toggleMic(),
    interrupt: session.interrupt,
    retry: () => void session.retry(),
    resetSession: () => session.resetSession(true),
    refreshAssets: () => void session.refreshAssets(),
    selectPreset: (id) => void session.selectPreset(id),
    switchVoice: (id) => void session.switchVoice(id),
    generateAvatar: (name) => void session.generateAvatar(name),
    deleteAvatar: session.deleteAvatar,
    dance: () => void session.dance(),
    tapPlay: session.tapPlay,
    clearRecommendation: session.clearRecommendation,
    note: session.note,
  }
}
