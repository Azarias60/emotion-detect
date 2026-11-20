/**
 * 照片制作数字人会话（application 层）：上传 → 起名 → 后台产线 → 轮询进度。
 * 编排纯状态：轮询节奏由 app/ 层 tick 驱动；网络在 api/avatarStudio.ts。
 *
 * 历史教训（已内化）：
 * - 离开页面必须停轮询，回到页面 resumeRunning() 把最新进度接回来；
 * - 只接 6 小时内有更新的 running，超过当僵尸不接（2026-11-02 假跑进度条事故）；
 * - 取消失败不能假装取消成功。
 */
import {
  initialStudioState,
  pickResumable,
  type StudioJobStatus,
  type StudioPort,
  type StudioSettled,
  type StudioState,
} from '../domain/avatarStudio'

/** 进度轮询间隔（ms）：与原版 5 秒同节奏。 */
export const STUDIO_POLL_MS = 5000

export interface AvatarStudioSession {
  subscribe: (listener: () => void) => () => void
  getSnapshot: () => StudioState
  /** 由页面按固定节奏驱动（进度轮询） */
  tick: (now: number) => void
  /** 上传本地照片，返回 job_id；失败置 error 并返回空串 */
  uploadPhoto: (file: File) => Promise<string>
  /** 直链抓取照片（网上搜来的全身照），与上传同一路径进入起名环节 */
  fetchPhotoByUrl: (url: string) => Promise<string>
  /** 创建制作任务（后台 1~2.5 小时），成功后开始轮询 */
  create: (jobId: string, name: string, displayName: string) => Promise<boolean>
  /** 取消制作：后端先杀产线进程再删任务目录 */
  cancel: () => Promise<boolean>
  /** 恢复正在跑的任务（刷新页面后进度不丢）；顺带给出中断提示 */
  resumeRunning: () => Promise<void>
  /** 任务终态回调（完成/失败/中断）：页面据此刷新数字人列表、自动绑音色 */
  onSettled: (callback: (result: StudioSettled) => void) => void
  /** 已提交的 ASCII id（音色自动绑定用） */
  submittedName: () => string
  reset: () => void
  dispose: () => void
}

export function createAvatarStudioSession(port: StudioPort): AvatarStudioSession {
  let state = initialStudioState
  let disposed = false
  let lastPollAt = 0
  let polling = false
  let settledCallback: ((result: StudioSettled) => void) | null = null

  const listeners = new Set<() => void>()
  const emit = () => listeners.forEach((listener) => listener())
  const set = (patch: Partial<StudioState>) => {
    state = { ...state, ...patch }
    emit()
  }

  const stopPolling = () => {
    polling = false
  }

  const pollOnce = async () => {
    const id = state.jobId
    if (!id || disposed) return
    const data = await port.job(id)
    if (disposed || !data) return
    const status = (data.status ?? '') as StudioJobStatus
    set({
      status,
      stage: data.stage ?? '',
      stageIndex: data.stage_index ?? -1,
      total: data.total || 6,
      message: data.message ?? '',
    })
    // 终态都停轮询；interrupted = 进程不在了但没写 ok（僵尸），
    // 前端据此提示重新上传，不再假跑进度条
    if (status === 'completed' || status === 'failed' || status === 'interrupted') {
      stopPolling()
      settledCallback?.({ status, message: data.message ?? '' })
    }
  }

  const startPolling = () => {
    polling = true
    lastPollAt = 0
    void pollOnce()
  }

  const reset = () => {
    stopPolling()
    set({
      jobId: '',
      submittedName: '',
      status: '',
      stage: '',
      stageIndex: -1,
      message: '',
      error: '',
    })
  }

  const uploadPhoto = async (file: File) => {
    set({ uploading: true, error: '' })
    reset()
    try {
      const jobId = await port.uploadPhoto(file)
      set({ jobId, uploading: false })
      return jobId
    } catch (error) {
      set({ uploading: false, error: (error as Error)?.message || '照片上传失败' })
      return ''
    }
  }

  const fetchPhotoByUrl = async (url: string) => {
    const trimmed = (url || '').trim()
    if (!trimmed) {
      set({ error: '请输入图片链接' })
      return ''
    }
    set({ uploading: true, error: '' })
    reset()
    try {
      const jobId = await port.fetchPhotoByUrl(trimmed)
      set({ jobId, uploading: false })
      return jobId
    } catch (error) {
      set({ uploading: false, error: (error as Error)?.message || '链接抓取失败' })
      return ''
    }
  }

  const create = async (jobId: string, name: string, displayName: string) => {
    set({ creating: true, error: '' })
    try {
      await port.createJob(jobId, name, displayName)
      set({ creating: false, submittedName: name, status: 'running' })
      startPolling()
      return true
    } catch (error) {
      set({ creating: false, error: (error as Error)?.message || '创建任务失败' })
      return false
    }
  }

  const cancel = async () => {
    const id = state.jobId
    stopPolling()
    if (!id) {
      reset()
      return true
    }
    try {
      await port.cancelJob(id)
    } catch (error) {
      set({ error: (error as Error)?.message || '取消失败，任务可能还在后台运行' })
      polling = true
      return false
    }
    reset()
    return true
  }

  const resumeRunning = async () => {
    let jobs
    try {
      jobs = await port.jobs()
    } catch {
      return // 后端不在也无所谓
    }
    if (disposed) return
    const { running, stale } = pickResumable(jobs, Date.now())
    if (running?.job_id) {
      set({
        interruptedNotice: '',
        jobId: running.job_id,
        status: 'running',
        submittedName: running.name ?? '',
      })
      startPolling()
      return
    }
    set({
      interruptedNotice: stale
        ? `检测到未完成的任务「${stale.name || '未命名'}」（后台进程已不在），需要重新上传照片开始`
        : '',
    })
  }

  const tick = (now: number) => {
    if (disposed || !polling) return
    if (now - lastPollAt >= STUDIO_POLL_MS) {
      lastPollAt = now
      void pollOnce()
    }
  }

  return {
    subscribe: (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    getSnapshot: () => state,
    tick,
    uploadPhoto,
    fetchPhotoByUrl,
    create,
    cancel,
    resumeRunning,
    onSettled: (callback) => {
      settledCallback = callback
    },
    submittedName: () => state.submittedName,
    reset,
    dispose: () => {
      disposed = true
      stopPolling()
    },
  }
}
