import { useEffect, useMemo, useSyncExternalStore } from 'react'
import { createGuideSession, type GuideSession } from '../application/guideSession'
import type { GuideBinding, GuideTransport } from '../domain/guideSession'

const TICK_MS = 500

/** 导览模块 → 本仓库路由（引擎推荐 module 名 → 页面路径）。 */
export const GUIDE_MODULE_PATHS: Record<string, string> = {
  health: '/detection?mode=health',
  fatigue: '/detection?mode=fatigue',
  emotion: '/detection?mode=emotion',
  companion: '/digital-human',
}

type CachedGuideSession = {
  session: GuideSession
  /** 模块跳转回调随每次挂载更新；会话本身不重建 */
  jump: { current: (module: string) => void }
  pagehideBound: boolean
}

/**
 * 每个 transport 一条常驻会话（旧 Vue 版的"模块级单例"口径）：
 * SPA 换页只 suspend（停麦 + 停语音），WebSocket 与引擎侧会话记忆保留；
 * 回到首页 attach 即续聊，不再重新连接、不再重新 configure 一轮长等待。
 * 真正的销毁只发生在整页卸载（pagehide）或退出登录（App 层 closeAll）。
 */
const sessions = new WeakMap<GuideTransport, CachedGuideSession>()

function acquireGuideSession(
  transport: GuideTransport,
  onModuleJump: (module: string) => void,
): CachedGuideSession {
  let entry = sessions.get(transport)
  if (!entry) {
    const jump = { current: onModuleJump }
    const session = createGuideSession(transport, {
      pageToken: crypto.randomUUID?.() ?? String(Math.random()),
      onModuleJump: (module) => jump.current(module),
    })
    entry = { session, jump, pagehideBound: false }
    sessions.set(transport, entry)
  }
  // 回调经可变引用转发：父组件每次渲染换新函数也不会重建会话
  entry.jump.current = onModuleJump
  if (!entry.pagehideBound) {
    entry.pagehideBound = true
    // 整页卸载（关标签/刷新）才销毁；bfcache 恢复（persisted）时保持会话
    window.addEventListener('pagehide', (event) => {
      if (event.persisted) return
      if (sessions.get(transport) !== entry) return
      sessions.delete(transport)
      entry.session.dispose()
    })
  }
  return entry
}

/**
 * 导览会话的 React 绑定（app 层）：传输由组合根注入；模块跳转经
 * onModuleJump 回调映射成本仓库路由（引擎只给模块名）。
 */
export function useGuideSession(
  transport: GuideTransport,
  onModuleJump: (module: string) => void,
): GuideBinding {
  const entry = useMemo(() => acquireGuideSession(transport, onModuleJump), [transport])
  const session = entry.session
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot)

  useEffect(() => {
    // 播放边沿必须挂在"已提交"的会话上：建会话是在渲染期（useMemo 工厂
    // 在 StrictMode 下会被调用两次并丢弃其一），被丢弃的会话会抢走回调，
    // 于是语音在播、状态条却永远不动（实测踩过）。
    const offPlayback = transport.onPlayback((active) => session.setPlayback(active))
    // 自动播放策略：无手势时采集上下文可能被挂起（PCM 不出），
    // 首次点击/按键/切回标签页时恢复；开麦重试由 tick 继续兜底
    const resumeAudio = () => transport.resume?.()
    const onVisible = () => { if (!document.hidden) resumeAudio() }
    document.addEventListener('pointerdown', resumeAudio)
    document.addEventListener('keydown', resumeAudio)
    document.addEventListener('visibilitychange', onVisible)
    const timer = setInterval(() => session.tick(performance.now()), TICK_MS)
    // 回到首页：复用仍活着的 WS，只恢复麦克风与播放状态
    session.attach()
    return () => {
      offPlayback()
      document.removeEventListener('pointerdown', resumeAudio)
      document.removeEventListener('keydown', resumeAudio)
      document.removeEventListener('visibilitychange', onVisible)
      clearInterval(timer)
      // SPA 离开首页：停麦 + 停语音（别的页面不该被暖暖的声音追着放），
      // 但不关 WS——回来时 attach 秒续，不需要重新连接引擎。
      session.suspend()
    }
  }, [session, transport])

  return {
    state,
    sendText: session.sendText,
    toggleMic: () => void session.toggleMic(),
    interrupt: () => session.interrupt(),
    resetSession: session.resetSession,
  }
}
