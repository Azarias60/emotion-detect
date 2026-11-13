/**
 * 照片制作数字人客户端：上传照片 → 创建后台产线任务 → 轮询进度。
 *
 * 后端（8081，dev 走 /python-api 代理）：
 *   POST /dh/avatar/photo       上传照片，返回 job_id
 *   POST /dh/avatar/jobs        创建制作任务（后台 1~2.5 小时）
 *   GET  /dh/avatar/jobs/{id}   查进度（stage/stage_index/total/message/status）
 */
import { onBeforeUnmount, ref } from 'vue'

const API = (import.meta.env.VITE_PY_API_URL || '/python-api').replace(/\/+$/, '')

export function useAvatarStudio() {
  const jobId = ref('')
  const jobStatus = ref('') // running / completed / failed
  const jobStage = ref('')
  const jobStageIndex = ref(-1)
  const jobTotal = ref(6)
  const jobMessage = ref('')
  const interruptedNotice = ref('') // 有中断的旧任务时给用户一句提示（不接、不假跑）
  const uploading = ref(false)
  // 本次提交的数字人 ASCII id（音色自动绑定用）——progress.json 里的 name 是
  // 中文显示名、job_id 在轮询列表里会被 uploads 目录 id 覆盖，都不可靠
  const submittedName = ref('')
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
    submittedName.value = ''
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
      submittedName.value = name
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
      // 卸载守卫（2026-11-13 DeepSeek 审查）：离开页面时在途的 fetch 回来后
      // 不能再往已销毁组件的状态里写，onSettled 也不能再触发
      if (disposed) return null
      jobStatus.value = data.status || ''
      jobStage.value = data.stage || ''
      jobStageIndex.value = data.stage_index ?? -1
      jobTotal.value = data.total || 6
      jobMessage.value = data.message || ''
      // 终态都停轮询；interrupted = 进程不在了但没写 ok（僵尸），
      // 后端 2026-11-09 起会标出来，前端据此提示重新上传，不再假跑进度条
      if (data.status === 'completed' || data.status === 'failed'
          || data.status === 'interrupted') {
        stopPolling()
        onSettled?.(data)
      }
      return data
    } catch {
      return null
    }
  }

  /** 任务终态回调（完成/失败/中断）：页面据此自动刷新数字人列表 */
  let onSettled = null
  function onJobSettled(fn) {
    onSettled = fn
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

  // 离开页面必须停轮询（2026-11-13 修复）：以前没有卸载钩子，5 秒一次的后台
  // fetch 会跟着用户到永远，终态回调还会往已销毁组件的数据里 push。
  // 制作任务本身是后端后台进程，停轮询不影响它继续跑——回到页面
  // resumeRunning() 会把最新进度接回来。
  let disposed = false
  onBeforeUnmount(() => {
    disposed = true
    stopPolling()
  })

  /** 挂载时调用：恢复正在跑的任务（刷新页面后进度不丢）。
   *  取 running 中 ts 最新的那个，且只接 6 小时内有更新的——超过就当僵尸
   *  不接（后端现在会把进程已死的标成 interrupted，这里再兜一层）。
   *  历史教训：2026-11-02 页面一直显示 2/6，就是接到了几天前的死任务。
   *  若没有 running 但有新鲜的 interrupted，给出提示但不接（不假跑进度条）。 */
  async function resumeRunning() {
    try {
      const res = await fetch(`${API}/dh/avatar/jobs`)
      if (!res.ok) return
      const data = await res.json()
      const fresh = Date.now() / 1000 - 6 * 3600
      const jobs = data.jobs || []
      const running = jobs
        .filter((j) => j.status === 'running' && (j.ts || 0) > fresh)
        .sort((a, b) => (b.ts || 0) - (a.ts || 0))[0]
      if (running) {
        interruptedNotice.value = ''
        jobId.value = running.job_id || ''
        startPolling()
        return
      }
      const stale = jobs
        .filter((j) => j.status === 'interrupted' && (j.ts || 0) > fresh)
        .sort((a, b) => (b.ts || 0) - (a.ts || 0))[0]
      interruptedNotice.value = stale
        ? `检测到未完成的任务「${stale.name || '未命名'}」（后台进程已不在），需要重新上传照片开始`
        : ''
    } catch {
      /* 后端不在也无所谓 */
    }
  }

  /** 取消制作：后端先杀产线进程再删任务目录（2026-11-09 修复前只删目录，
   *  子进程会把进度重新写回来，任务还在后台跑）。 */
  async function cancelJob() {
    const id = jobId.value
    stopPolling()
    if (!id) {
      reset()
      return true
    }
    try {
      const res = await fetch(`${API}/dh/avatar/jobs/${id}`, { method: 'DELETE' })
      // 删除失败不能假装取消成功（2026-11-13 DeepSeek 审查）：后端任务还在跑，
      // UI 却显示已取消，用户会以为停了
      if (!res.ok) {
        error.value = `取消失败（HTTP ${res.status}），任务可能还在后台运行`
        startPolling()
        return false
      }
    } catch (e) {
      error.value = `取消失败：${e.message || '网络错误'}，任务可能还在后台运行`
      startPolling()
      return false
    }
    reset()
    return true
  }

  const stageLabel = () => STAGE_LABELS[jobStage.value] || jobStage.value

  return {
    jobId, jobStatus, jobStage, jobStageIndex, jobTotal, jobMessage,
    interruptedNotice, submittedName,
    uploading, creating, error,
    uploadPhoto, fetchPhotoByUrl, createJob, pollOnce, startPolling, stopPolling,
    resumeRunning, cancelJob, onJobSettled, stageLabel, reset,
  }
}
