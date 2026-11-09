import axios from 'axios'

export const API_BASE = (import.meta.env?.VITE_API_BASE_URL || '/python-api').replace(/\/$/, '')
export const videoFeedUrl = `${API_BASE}/video_feed`

export function errorMessage(error) {
  const data = error?.response?.data || error
  const detail = data?.detail || data?.msg || data?.message || error?.message
  if (Array.isArray(detail)) return detail.map((item) => item.msg || String(item)).join('；')
  return typeof detail === 'string' ? detail : '请求失败，请稍后重试'
}

// 传输层只校验响应；提示和认证失效处理由各客户端配置。
export function createHttpClient({ timeout = 30000, token, onError } = {}) {
  const client = axios.create({ baseURL: API_BASE, timeout })
  if (token)
    client.interceptors.request.use((config) => {
      const value = token()
      if (value) config.headers.Authorization = `Bearer ${value}`
      return config
    })
  client.interceptors.response.use((response) => {
    const body = response.data
    if (body?.code === 0 || body?.code === 200) return body
    throw Object.assign(new Error(errorMessage(body)), { response })
  })
  client.interceptors.response.use(undefined, (cause) => {
    if (axios.isCancel(cause)) return Promise.reject(cause)
    const error = Object.assign(new Error(errorMessage(cause)), { cause, response: cause.response })
    onError?.(error)
    return Promise.reject(error)
  })
  return client
}

export const detectionClient = createHttpClient({ timeout: 120000 })
export const responseData = (request) => request.then((body) => body.data)
