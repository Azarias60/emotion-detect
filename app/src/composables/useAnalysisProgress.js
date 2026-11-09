import { onScopeDispose, ref } from 'vue'

/** Smooth simulated progress for model analysis. It never reaches 100 until the
 * request has returned, so the final transition is tied to real completion. */
export function useAnalysisProgress() {
  const value = ref(0)
  let timer = null
  let finishTimer
  let resolveFinish

  function start(initial = 8) {
    stop()
    value.value = initial
    timer = setInterval(() => {
      if (value.value < 92) value.value += value.value < 65 ? 2 : 1
    }, 420)
  }

  async function finish() {
    stop()
    value.value = 100
    await new Promise((resolve) => {
      resolveFinish = resolve
      finishTimer = setTimeout(() => {
        resolveFinish = null
        resolve()
      }, 420)
    })
  }

  function fail() {
    stop()
    value.value = 0
  }

  function stop() {
    if (timer) clearInterval(timer)
    timer = null
  }

  onScopeDispose(() => {
    stop()
    clearTimeout(finishTimer)
    resolveFinish?.()
  })
  return { value, start, finish, fail, stop }
}
