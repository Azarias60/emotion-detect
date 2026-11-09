import { onBeforeUnmount, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'

export function useDetectionGuard(active) {
  const warning = '检测或分析尚未完成，离开页面将丢失当前数据。确定离开吗？'
  const beforeUnload = (event) => {
    if (!active.value) return
    event.preventDefault()
    event.returnValue = warning
  }

  watch(
    active,
    (value) => {
      document.body.classList.toggle('detection-locked', value)
    },
    { immediate: true }
  )

  window.addEventListener('beforeunload', beforeUnload)
  onBeforeRouteLeave(() => !active.value || window.confirm(warning))
  onBeforeUnmount(() => {
    window.removeEventListener('beforeunload', beforeUnload)
    document.body.classList.remove('detection-locked')
  })
}
