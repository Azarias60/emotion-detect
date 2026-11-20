/**
 * 照片制作数字人（产线）领域模型：上传照片 → 创建后台任务 → 轮询进度。
 *
 * 后端（Emotion-Detect-py，8081）接口：
 *   POST /dh/avatar/photo        上传照片，返回 job_id
 *   POST /dh/avatar/photo-url    直链抓取照片，返回 job_id
 *   POST /dh/avatar/jobs         创建制作任务（后台 1~2.5 小时）
 *   GET  /dh/avatar/jobs/{id}    查进度（stage/stage_index/total/message/status）
 *   DELETE /dh/avatar/jobs/{id}  取消（先杀产线进程再删目录）
 * 本文件只定义状态与阶段口径，网络在 api/avatarStudio.ts、编排在 application 层。
 */

export type StudioJobStatus = '' | 'running' | 'completed' | 'failed' | 'interrupted'

/** GET /dh/avatar/jobs/{id} 的进度快照。 */
export interface StudioJobSnapshot {
  status?: StudioJobStatus | string
  stage?: string
  stage_index?: number
  total?: number
  message?: string
}

/** GET /dh/avatar/jobs 的任务摘要（恢复进行中任务用）。 */
export interface StudioJobSummary {
  job_id?: string
  name?: string
  status?: StudioJobStatus | string
  ts?: number
}

export interface StudioState {
  jobId: string
  status: StudioJobStatus
  stage: string
  stageIndex: number
  total: number
  message: string
  /** 有中断的旧任务时给用户一句提示（不接、不假跑） */
  interruptedNotice: string
  uploading: boolean
  creating: boolean
  error: string
  /** 本次提交的数字人 ASCII id（音色自动绑定用；中文显示名绑过去会 404） */
  submittedName: string
}

export const initialStudioState: StudioState = {
  jobId: '',
  status: '',
  stage: '',
  stageIndex: -1,
  total: 6,
  message: '',
  interruptedNotice: '',
  uploading: false,
  creating: false,
  error: '',
  submittedName: '',
}

/** 客户视角的阶段名（对齐产线 STAGES，但说人话）。 */
export const STAGE_LABELS: Record<string, string> = {
  体检照片: '检查照片',
  生成动作段: '生成动作（约 1 小时）',
  挑选与稳定: '稳定画面',
  生成过渡: '生成过渡',
  质量检查: '质量检查',
  组装上架: '组装上架',
}

export function stageLabel(stage: string): string {
  return STAGE_LABELS[stage] || stage
}

/** 制作平台端口：网络实现在 api/avatarStudio.ts，应用层只依赖本接口。 */
export interface StudioPort {
  uploadPhoto(file: File): Promise<string>
  fetchPhotoByUrl(url: string): Promise<string>
  createJob(jobId: string, name: string, displayName: string): Promise<void>
  job(jobId: string): Promise<StudioJobSnapshot | null>
  jobs(): Promise<StudioJobSummary[]>
  /** 取消失败时抛错（后端任务还在跑，UI 不能假装已取消） */
  cancelJob(jobId: string): Promise<void>
}

/** 任务终态（完成/失败/中断）的统一载荷。 */
export interface StudioSettled {
  status: StudioJobStatus
  message: string
}

/** 恢复进行中任务时只接 6 小时内有更新的：超过当僵尸不接（2026-11-02 教训）。 */
export const RESUME_FRESH_MS = 6 * 3600 * 1000

/** 从任务列表里挑最新一个可恢复的 running；没有则返回最近中断的（供提示）。 */
export function pickResumable(jobs: StudioJobSummary[], now: number): {
  running?: StudioJobSummary
  stale?: StudioJobSummary
} {
  const fresh = jobs.filter((job) => (job.ts ?? 0) * 1000 > now - RESUME_FRESH_MS)
  const running = [...fresh]
    .filter((job) => job.status === 'running')
    .sort((a, b) => (b.ts ?? 0) - (a.ts ?? 0))[0]
  if (running) return { running }
  const stale = [...fresh]
    .filter((job) => job.status === 'interrupted')
    .sort((a, b) => (b.ts ?? 0) - (a.ts ?? 0))[0]
  return { stale }
}
