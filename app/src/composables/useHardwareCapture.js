import { onScopeDispose, ref } from 'vue'
import * as hardwareApi from '../api/hardware.js'

/** 拥有本页面启动的采集；轮询串行执行，离开页面停止采集。 */
export function useHardwareCapture({ api = hardwareApi, poll = false } = {}) {
  const recording = ref(false)
  const liveData = ref(null)
  let disposed = false
  let starting = false
  let ownsCapture = false
  let polling = false
  let stopPromise
  let timer
  let pollController

  function stopPolling() {
    polling = false
    clearTimeout(timer)
    pollController?.abort()
  }
  async function pollData() {
    if (!polling || disposed) return
    const controller = new AbortController()
    pollController = controller
    try {
      const data = await api.captureData({ signal: controller.signal })
      if (polling && !disposed && !controller.signal.aborted) liveData.value = data
    } catch {
      /* 下一次轮询可恢复；不影响采集。 */
    }
    if (polling && !disposed && !controller.signal.aborted) timer = setTimeout(pollData, 250)
  }
  async function start(duration = 60) {
    if (disposed || starting || recording.value) return false
    starting = true
    liveData.value = null
    try {
      // 不取消启动请求：如果离开页面时它才成功，仍须补发停止请求。
      if (ownsCapture) await stop()
      if (disposed) return false
      await api.startCapture(duration)
      ownsCapture = true
      recording.value = true
      if (disposed) {
        await stop()
        return false
      }
      if (poll) {
        polling = true
        void pollData()
      }
      return true
    } finally {
      starting = false
    }
  }
  async function stop() {
    stopPolling()
    if (stopPromise) return stopPromise
    if (!ownsCapture) return
    stopPromise = (async () => {
      const data = await api.stopCapture()
      if (poll && !disposed) {
        try {
          // 停止窗口后再取一次设备快照，确保图表包含会话末尾的硬件样本。
          liveData.value = await api.captureData()
        } catch {
          /* 保留最后一次成功轮询的数据。 */
        }
      }
      ownsCapture = false
      return data
    })().finally(() => {
      recording.value = false
      stopPromise = undefined
    })
    return stopPromise
  }
  onScopeDispose(() => {
    disposed = true
    stopPolling()
    void stop().catch(() => {
      /* 设备仍受后端录制时限约束。 */
    })
  })
  return { recording, liveData, start, stop }
}
