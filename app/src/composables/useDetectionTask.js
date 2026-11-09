import { computed, onScopeDispose, ref } from 'vue'
import { useAnalysisProgress } from './useAnalysisProgress.js'

const activePhases = new Set([
  'preparing',
  'capturing',
  'consulting',
  'uploading',
  'analyzing',
  'tongue-capturing',
  'tongue-analyzing',
])

/** 请求生命周期：防重入、取消、错误与进度；业务数据由功能 composable 持有。 */
export function useDetectionTask(initialPhase = 'idle') {
  const phase = ref(initialPhase)
  const errorMessage = ref('')
  const running = ref(false)
  const progress = useAnalysisProgress()
  const busy = computed(() => running.value || activePhases.has(phase.value))
  let disposed = false
  let controller

  async function run(
    job,
    { pending = 'analyzing', success = 'complete', failure = 'error', showProgress = true } = {}
  ) {
    if (disposed || running.value) return
    running.value = true
    errorMessage.value = ''
    phase.value = pending
    controller = new AbortController()
    if (showProgress) progress.start(10)
    try {
      const value = await job(controller.signal)
      if (disposed) return
      if (showProgress) await progress.finish()
      if (disposed) return
      phase.value = success
      return value
    } catch (error) {
      if (!disposed) {
        progress.fail()
        errorMessage.value = error.message || '检测失败，请重试'
        phase.value = failure
      }
    } finally {
      running.value = false
      controller = null
    }
  }

  onScopeDispose(() => {
    disposed = true
    controller?.abort()
  })
  return { phase, errorMessage, running, busy, analysisProgress: progress.value, run }
}
