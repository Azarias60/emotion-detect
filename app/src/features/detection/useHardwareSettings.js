import { computed, onMounted, onScopeDispose, reactive, ref } from 'vue'
import { getHardwareMode, setHardwareMode } from '../../api/hardware.js'

export function useHardwareSettings() {
  const isOpen = ref(false)
  const loading = ref(false)
  const message = reactive({ text: '', type: 'info' })

  const statusData = reactive({
    isConnected: false,
    isRecording: false,
    transport: {
      type: 'wifi',
      host: '192.168.8.11',
      tcp_port: 8888,
      udp_port: 8889,
      port: 'COM3',
      baudrate: 2000000,
    },
  })

  const selectedMode = ref('wifi')

  const wifiForm = reactive({
    host: localStorage.getItem('cug_mcu_wifi_host') || '192.168.8.11',
    tcp_port: Number(localStorage.getItem('cug_mcu_wifi_tcp_port')) || 8888,
    udp_port: Number(localStorage.getItem('cug_mcu_wifi_udp_port')) || 8889,
  })

  const serialForm = reactive({
    port: localStorage.getItem('cug_mcu_serial_port') || 'COM3',
    baudrate: Number(localStorage.getItem('cug_mcu_serial_baudrate')) || 2000000,
  })

  const baudrateOptions = [2000000, 1000000, 921600, 460800, 115200, 9600]
  const comPortSuggestions = [
    'COM1',
    'COM2',
    'COM3',
    'COM4',
    'COM5',
    'COM6',
    'COM7',
    'COM8',
    'COM9',
  ]

  const badgeText = computed(() => {
    const isWifi = statusData.transport?.type === 'wifi'
    if (isWifi) {
      const host = statusData.transport?.host || wifiForm.host
      return `WiFi ${host}`
    }
    const port = statusData.transport?.port || serialForm.port
    return `串口 ${port}`
  })

  let pollTimer = null
  let refreshTimer = null
  let disposed = false
  let statusController

  async function fetchStatus(syncTab = false) {
    try {
      statusController?.abort()
      statusController = new AbortController()
      const data = await getHardwareMode({ signal: statusController.signal })
      if (disposed) return
      if (data) {
        statusData.isConnected = !!data.is_connected
        statusData.isRecording = !!data.is_recording
        if (data.transport) {
          statusData.transport = data.transport
          // 仅在明确要求同步 Tab 时（如打开弹窗时定位当前模式）才更新 selectedMode 与表单
          // 周期轮询期间坚决不覆盖用户正在点击浏览的 Tab 或正在编辑的内容
          if (syncTab && data.transport.type) {
            selectedMode.value = data.transport.type
            if (data.transport.type === 'wifi') {
              if (data.transport.host) wifiForm.host = data.transport.host
              if (data.transport.tcp_port) wifiForm.tcp_port = data.transport.tcp_port
              if (data.transport.udp_port) wifiForm.udp_port = data.transport.udp_port
            } else if (data.transport.type === 'serial') {
              if (data.transport.port) serialForm.port = data.transport.port
              if (data.transport.baudrate) serialForm.baudrate = data.transport.baudrate
            }
          }
        }
      }
    } catch (err) {
      if (disposed || err.code === 'ERR_CANCELED' || err.name === 'CanceledError') return
      statusData.isConnected = false
      if (syncTab) {
        message.type = 'error'
        message.text = err.message || '无法连接后端服务'
      }
    }
  }

  async function handleApplySwitch() {
    if (loading.value) return
    loading.value = true
    message.text = '正在更新后端设备连接配置...'
    message.type = 'info'

    const payload = { mode: selectedMode.value }
    if (selectedMode.value === 'wifi') {
      payload.host = wifiForm.host.trim()
      payload.tcp_port = Number(wifiForm.tcp_port)
      payload.udp_port = Number(wifiForm.udp_port)
    } else {
      payload.port = serialForm.port.trim()
      payload.baudrate = Number(serialForm.baudrate)
    }

    try {
      const data = await setHardwareMode(payload)
      if (disposed) return
      if (data) {
        if (payload.mode === 'wifi') {
          localStorage.setItem('cug_mcu_wifi_host', payload.host)
          localStorage.setItem('cug_mcu_wifi_tcp_port', String(payload.tcp_port))
          localStorage.setItem('cug_mcu_wifi_udp_port', String(payload.udp_port))
        } else {
          localStorage.setItem('cug_mcu_serial_port', payload.port)
          localStorage.setItem('cug_mcu_serial_baudrate', String(payload.baudrate))
        }
        message.type = 'success'
        message.text = '连接配置已提交，等待设备确认输出模式...'
        await fetchStatus(false)
        if (disposed) return
        clearTimeout(refreshTimer)
        refreshTimer = setTimeout(() => fetchStatus(false), 1500)
      } else {
        message.type = 'error'
        message.text = data?.msg || '切换失败，请检查参数'
      }
    } catch (err) {
      message.type = 'error'
      message.text = err.message || '无法连接后端服务'
    } finally {
      loading.value = false
    }
  }

  function openModal() {
    message.text = ''
    isOpen.value = true
    // 每次打开弹窗时，按设定默认定位到当前后端运行的模式，打开后用户点击切换则绝不弹回
    fetchStatus(true)
  }

  function closeModal() {
    isOpen.value = false
  }

  onMounted(() => {
    fetchStatus(true)
    pollTimer = setInterval(() => fetchStatus(false), 5000)
  })

  onScopeDispose(() => {
    disposed = true
    clearInterval(pollTimer)
    clearTimeout(refreshTimer)
    statusController?.abort()
  })
  return {
    isOpen,
    loading,
    message,
    statusData,
    selectedMode,
    wifiForm,
    serialForm,
    baudrateOptions,
    comPortSuggestions,
    badgeText,
    openModal,
    closeModal,
    handleApplySwitch,
    fetchStatus,
  }
}
