import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react'
import { createAvatarStudioSession, type AvatarStudioSession } from '../application/avatarStudio'
import type { StudioPort, StudioState, StudioSettled } from '../domain/avatarStudio'

const TICK_MS = 500
const DISPOSE_GRACE_MS = 300

export interface AvatarStudioBinding {
  state: StudioState
  uploadPhoto: (file: File) => Promise<string>
  fetchPhotoByUrl: (url: string) => Promise<string>
  create: (jobId: string, name: string, displayName: string) => Promise<boolean>
  cancel: () => Promise<boolean>
  resumeRunning: () => void
  /** 任务终态回调（完成/失败/中断） */
  onSettled: (callback: (result: StudioSettled) => void) => void
  submittedName: () => string
  reset: () => void
}

/**
 * 制作数字人会话的 React 绑定：进度轮询由本 hook 的 tick 驱动；
 * 任务本身跑在后端后台进程里，离开页面停轮询不影响它继续跑。
 */
export function useAvatarStudio(port: StudioPort): AvatarStudioBinding {
  const session: AvatarStudioSession = useMemo(() => createAvatarStudioSession(port), [port])
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
      disposeTimer.current = window.setTimeout(() => {
        session.dispose()
        disposeTimer.current = null
      }, DISPOSE_GRACE_MS)
    }
  }, [session])

  return {
    state,
    uploadPhoto: session.uploadPhoto,
    fetchPhotoByUrl: session.fetchPhotoByUrl,
    create: session.create,
    cancel: session.cancel,
    resumeRunning: () => void session.resumeRunning(),
    onSettled: session.onSettled,
    submittedName: session.submittedName,
    reset: session.reset,
  }
}
