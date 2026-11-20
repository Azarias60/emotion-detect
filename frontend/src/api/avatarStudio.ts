/**
 * 照片制作数字人的平台实现（api）：后端在 Emotion-Detect-py（8081），
 * dev 经 Vite 代理 /emotion-api 访问。上传即预检（尺寸+正脸+人脸占比）。
 */
import type { StudioJobSnapshot, StudioJobSummary, StudioPort } from '../domain/avatarStudio'

const API_BASE = '/emotion-api'

export function createAvatarStudioPort(): StudioPort {
  const post = async (path: string, init: RequestInit): Promise<Record<string, unknown>> => {
    const response = await fetch(`${API_BASE}${path}`, init)
    const body = await response.json().catch(() => null) as (Record<string, unknown> | null)
    if (!response.ok) {
      // 后端错误统一放在 detail（FastAPI 惯例）
      const detail = body?.detail
      throw new Error(typeof detail === 'string' && detail ? detail : `接口 ${path} 返回 ${response.status}`)
    }
    return body ?? {}
  }

  const getJson = async (path: string): Promise<unknown> => {
    const response = await fetch(`${API_BASE}${path}`)
    if (!response.ok) throw new Error(`接口 ${path} 返回 ${response.status}`)
    return response.json()
  }

  return {
    async uploadPhoto(file) {
      const form = new FormData()
      form.append('file', file)
      const body = await post('/dh/avatar/photo', { method: 'POST', body: form })
      return String(body.job_id ?? '')
    },

    async fetchPhotoByUrl(url) {
      const body = await post('/dh/avatar/photo-url', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
      })
      return String(body.job_id ?? '')
    },

    async createJob(jobId, name, displayName) {
      await post('/dh/avatar/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: jobId, name, display_name: displayName }),
      })
    },

    async job(jobId) {
      try {
        return (await getJson(`/dh/avatar/jobs/${encodeURIComponent(jobId)}`)) as StudioJobSnapshot
      } catch {
        return null
      }
    },

    async jobs() {
      const body = (await getJson('/dh/avatar/jobs').catch(() => null)) as { jobs?: StudioJobSummary[] } | null
      return body?.jobs ?? []
    },

    async cancelJob(jobId) {
      const response = await fetch(`${API_BASE}/dh/avatar/jobs/${encodeURIComponent(jobId)}`, { method: 'DELETE' })
      if (!response.ok) {
        // 删除失败不能假装取消成功：后端任务还在跑，UI 会误导用户
        throw new Error(`取消失败（HTTP ${response.status}），任务可能还在后台运行`)
      }
    },
  }
}
