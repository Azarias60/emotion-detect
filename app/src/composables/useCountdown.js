import { computed, onScopeDispose, ref } from 'vue'

export function useCountdown(duration = 60) {
  const remaining = ref(duration)
  const progress = computed(() => Math.round(((duration - remaining.value) / duration) * 100))
  let timer
  function stop() {
    clearInterval(timer)
    timer = undefined
  }
  function reset() {
    stop()
    remaining.value = duration
  }
  function start(onComplete) {
    reset()
    const deadline = Date.now() + duration * 1000
    timer = setInterval(() => {
      remaining.value = Math.max(0, Math.ceil((deadline - Date.now()) / 1000))
      if (remaining.value === 0) {
        stop()
        onComplete()
      }
    }, 250)
  }
  onScopeDispose(stop)
  return { remaining, progress, start, stop, reset }
}
