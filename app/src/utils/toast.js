// 极简全局消息提示：替代原 Element Plus ElMessage（仅为一个 toast 不值得引入整包）
const CONTAINER_ID = 'app-toast-container'

function ensureContainer() {
  let container = document.getElementById(CONTAINER_ID)
  if (!container) {
    container = document.createElement('div')
    container.id = CONTAINER_ID
    container.style.cssText =
      'position:fixed;top:24px;left:50%;transform:translateX(-50%);z-index:9999;' +
      'display:flex;flex-direction:column;gap:10px;align-items:center;pointer-events:none;'
    document.body.appendChild(container)
  }
  return container
}

export function toast(message, type = 'info', duration = 3000) {
  if (!message) return
  const item = document.createElement('div')
  const color = type === 'error' ? '#f56c6c' : type === 'success' ? '#67c23a' : '#409eff'
  item.textContent = message
  item.style.cssText =
    'max-width:70vw;padding:10px 20px;border-radius:6px;color:#fff;font-size:14px;' +
    `background:${color};box-shadow:0 4px 12px rgba(0,0,0,.25);opacity:0;transition:opacity .2s;`
  ensureContainer().appendChild(item)
  requestAnimationFrame(() => {
    item.style.opacity = '1'
  })
  setTimeout(() => {
    item.style.opacity = '0'
    setTimeout(() => item.remove(), 300)
  }, duration)
}

toast.error = (message, duration) => toast(message, 'error', duration)
toast.success = (message, duration) => toast(message, 'success', duration)
