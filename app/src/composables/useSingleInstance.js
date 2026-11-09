/**
 * 前端单实例闸门（2026-11-05）
 *
 * 后端同一时刻只允许一个前端页面实例（见 Emotion-Detect-py/routers/instance.py）。
 * 本模块在应用挂载**之前**就打开 /app-instance/ws 占位：
 *   · 新页面连上后，旧页面的连接会被服务端以 4001 顶掉；
 *   · 被顶掉的页面必须**整页跳转**到 /locked，而不是 SPA 路由跳转——导览页的
 *     实时语音客户端是模块级单例，只有整页重载才能把 WebSocket、AudioContext、
 *     麦克风轨道、定时器一次性拆干净；
 *   · 锁定页不再启动本闸门，点“在此窗口继续”后重新加载目标页，由目标页重新占位
 *     （另一个窗口随即被顶掉）。
 *
 * 断线（后端重启）自动重连；只有收到 4001/superseded 才会锁定。
 */

const WS_PATH = '/python-api/app-instance/ws'
export const LOCK_PATH = '/locked'

// 每次整页加载生成一个新实例 id：同一标签页刷新、复制标签页都算新实例，
// 由“最后连上者”接管，避免复制出的标签页共用 id 互相认为自己是主人。
const INSTANCE_ID = 'fe-' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36)
const HEARTBEAT_MS = 20000
const RETRY_MAX_MS = 5000

let socket = null
let retryTimer = 0
let heartbeatTimer = 0
let retries = 0
let started = false
let stopped = false

/** 当前页面是不是锁定页（锁定页绝不占位，否则会和主人互相顶）。 */
export function isLockedPage(path = location.pathname) {
  return path.replace(/\/+$/, '') === LOCK_PATH
}

function wsUrl() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}${WS_PATH}?id=${encodeURIComponent(INSTANCE_ID)}`
}

function clearTimers() {
  if (retryTimer) {
    clearTimeout(retryTimer)
    retryTimer = 0
  }
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer)
    heartbeatTimer = 0
  }
}

function startHeartbeat() {
  if (heartbeatTimer) clearInterval(heartbeatTimer)
  heartbeatTimer = setInterval(() => {
    if (socket && socket.readyState === WebSocket.OPEN) {
      try {
        socket.send('ping')
      } catch {}
    }
  }, HEARTBEAT_MS)
}

function scheduleRetry() {
  if (stopped || retryTimer) return
  const delay = Math.min(500 * 2 ** retries, RETRY_MAX_MS)
  retries += 1
  retryTimer = setTimeout(() => {
    retryTimer = 0
    connect()
  }, delay)
}

/** 被顶掉：停掉本页一切，整页去锁定页。from 记住原地址，方便“继续使用”回跳。 */
function lock() {
  if (stopped) return
  stopped = true
  clearTimers()
  try {
    socket?.close()
  } catch {}
  socket = null
  try {
    if (window.speechSynthesis) window.speechSynthesis.cancel()
    document.querySelectorAll('video, audio').forEach((el) => {
      try {
        el.pause()
        el.srcObject = null
      } catch {}
    })
  } catch {}
  const from = location.pathname + location.search + location.hash
  location.replace(`${LOCK_PATH}?from=${encodeURIComponent(from)}`)
}

function connect() {
  if (stopped) return
  try {
    socket = new WebSocket(wsUrl())
  } catch {
    socket = null
    scheduleRetry()
    return
  }

  socket.addEventListener('open', () => {
    retries = 0
    startHeartbeat()
  })

  socket.addEventListener('message', (event) => {
    try {
      const data = JSON.parse(event.data)
      if (data?.type === 'superseded') lock()
    } catch {}
  })

  socket.addEventListener('close', (event) => {
    socket = null
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer)
      heartbeatTimer = 0
    }
    if (stopped) return
    if (event.code === 4001) {
      lock()
      return
    }
    scheduleRetry()
  })

  // error 一定会紧跟 close，这里不做事，避免重复排重连
}

/**
 * 启动闸门。必须在 app.mount() 之前调用：先占住实例位，
 * 再让页面组件去连数字人引擎，避免新页面还没占位就先抢了麦克风。
 */
export function startInstanceGuard() {
  if (started || stopped) return
  if (isLockedPage()) return
  started = true
  connect()
}
