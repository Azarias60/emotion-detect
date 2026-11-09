/**
 * 照片制作数字人客户端：上传照片 → 创建后台产线任务 → 轮询进度。
 *
 * 后端（8081，dev 走 /python-api 代理）：
 *   POST /dh/avatar/photo       上传照片，返回 job_id
 *   POST /dh/avatar/jobs        创建制作任务（后台 1~2.5 小时）
 *   GET  /dh/avatar/jobs/{id}   查进度（stage/stage_index/total/message/status）
 */
import { ref } from 'vue'

const API = (import.meta.env.VITE_PY_API_URL || '/python-api').replace(/\/+$/, '')

export function useAvatarStudio() {
  const jobId = ref('')
  const jobStatus = ref('') // running / completed / failed
  const jobStage = ref('')
  const jobStageIndex = ref(-1)
  const jobTotal = ref(6)
  const jobMessage = ref('')
  const uploading = ref(false)
  const creating = ref(false)
  const error = ref('')
  let timer = null

  /** 客户视角的阶段名（对齐产线 STAGES，但说人话） */
  const STAGE_LABELS = {
    体检照片: '检查照片',
    生成动作段: '生成动作（约 1 小时）',
    挑选与稳定: '稳定画面',
    生成过渡: '生成过渡',
    质量检查: '质量检查',
    组装上架: '组装上架',
  }

  function reset() {
    jobId.value = ''
    jobStatus.value = ''
    jobStage.value = ''
    jobStageIndex.value = -1
    jobMessage.value = ''
    error.value = ''
  }

  async function uploadPhoto(file) {
    uploading.value = true
    error.value = ''
    reset()
    try {
      const form = new FormData()
      form.append('file', file)
      const res = await fetch(`${API}/dh/avatar/photo`, { method: 'POST', body: form })
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}))
        throw new Error(detail.detail || `上传失败（${res.status}）`)
      }
      const data = await res.json()
      jobId.value = data.job_id
      return data.job_id
    } catch (e) {
      error.value = e.message || '照片上传失败'
      return null
    } finally {
      uploading.value = false
    }
  }

  /** 直链下载照片（网上搜来的图片 URL）：后端抓取落地，产物同上传 */
  async function fetchPhotoByUrl(url) {
    const u = (url || '').trim()
    if (!u) {
      error.value = '请输入图片链接'
      return null
    }
    uploading.value = true
    error.value = ''
    reset()
    try {
      const res = await fetch(`${API}/dh/avatar/photo-url`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: u }),
      })
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}))
        throw new Error(detail.detail || `链接抓取失败（${res.status}）`)
      }
      const data = await res.json()
      jobId.value = data.job_id
      return data.job_id
    } catch (e) {
      error.value = e.message || '链接抓取失败'
      return null
    } finally {
      uploading.value = false
    }
  }

  async function createJob(name, displayName) {
    creating.value = true
    error.value = ''
    try {
      const res = await fetch(`${API}/dh/avatar/jobs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: jobId.value, name, display_name: displayName }),
      })
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}))
        throw new Error(detail.detail || `创建任务失败（${res.status}）`)
      }
      startPolling()
      return true
    } catch (e) {
      error.value = e.message || '创建任务失败'
      return false
    } finally {
      creating.value = false
    }
  }

  async function pollOnce(id = jobId.value) {
    if (!id) return null
    try {
      const res = await fetch(`${API}/dh/avatar/jobs/${id}`)
      if (!res.ok) return null
      const data = await res.json()
      jobStatus.value = data.status || ''
      jobStage.value = data.stage || ''
      jobStageIndex.value = data.stage_index ?? -1
      jobTotal.value = data.total || 6
      jobMessage.value = data.message || ''
      if (data.status === 'completed' || data.status === 'failed') {
        stopPolling()
      }
      return data
    } catch {
      return null
    }
  }

  function startPolling() {
    stopPolling()
    pollOnce()
    timer = setInterval(() => pollOnce(), 5000)
  }

  function stopPolling() {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  }

  /** 挂载时调用：恢复正在跑的任务（刷新页面后进度不丢）。
   *  取 running 中 ts 最新的那个：后端重启/宕机会留下"僵尸任务"——
   *  进程早死了但 progress.json 没写 ok，永远显示 running；按目录名取
   *  第一个会接到几天前的死任务上（2026-11-02 实测：页面一直显示 2/6
   *  就是接到了 10-24 的僵尸 webtest005）。ts 是进度文件最后写入时间，
   *  真正的任务一定最新。 */
  async function resumeRunning() {
    try {
      const res = await fetch(`${API}/dh/avatar/jobs`)
      if (!res.ok) return
      const data = await res.json()
      const running = (data.jobs || [])
        .filter((j) => j.status === 'running')
        .sort((a, b) => (b.ts || 0) - (a.ts || 0))[0]
      if (running) {
        jobId.value = running.job_id || ''
        startPolling()
      }
    } catch {
      /* 后端不在也无所谓 */
    }
  }

  const stageLabel = () => STAGE_LABELS[jobStage.value] || jobStage.value

  return {
    jobId, jobStatus, jobStage, jobStageIndex, jobTotal, jobMessage,
    uploading, creating, error,
    uploadPhoto, fetchPhotoByUrl, createJob, pollOnce, startPolling, stopPolling,
    resumeRunning, stageLabel, reset,
  }
}
