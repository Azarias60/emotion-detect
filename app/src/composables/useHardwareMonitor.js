import { onScopeDispose, ref } from 'vue'
import * as hardwareApi from '../api/hardware.js'

/** 页面存在期间持续读取硬件，与分析所需的录制窗口独立。 */
export function useHardwareMonitor({ api = hardwareApi } = {}) {
  const liveData = ref(null)
  let disposed = false
  let timer
  let controller
  let heartRateHistory = []

  function updateLiveData(data) {
    if (!data || data.is_connected === false) {
      heartRateHistory = []
      liveData.value = null
      return
    }
    const heartRate = data.heart_rate || {}
    if (Array.isArray(heartRate.live_hr_list)) {
      heartRateHistory = heartRate.live_hr_list.slice(-128)
    } else {
      // 兼容尚未提供实时历史的后端：按轮询记录实际读数，
      // 不使用只在录制窗口更新的 hr_list，也不按数值去重（恒定心率仍有趋势）。
      const latest = heartRate.latest_hr
      if (typeof latest === 'number' && Number.isFinite(latest) && latest >= 30 && latest <= 230)
        heartRateHistory = [...heartRateHistory, latest].slice(-128)
    }
    liveData.value = {
      ...data,
      heart_rate: { ...heartRate, live_hr_list: [...heartRateHistory] },
    }
  }

  async function refresh() {
    if (disposed) return
    controller = new AbortController()
    try {
      const data = await api.captureData({ signal: controller.signal, timeout: 2000 })
      if (!disposed) updateLiveData(data)
    } catch {
      // 失联时清空实时值，下一次轮询自动重试。
      if (!disposed) updateLiveData(null)
    } finally {
      if (!disposed) timer = setTimeout(refresh, 250)
    }
  }

  onScopeDispose(() => {
    disposed = true
    clearTimeout(timer)
    controller?.abort()
  })
  void refresh()
  return { liveData }
}
