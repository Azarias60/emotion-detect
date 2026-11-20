/** 后端真实 /api/v1 契约：成功 {code:0,message,data}；错误为 {detail}。 */
export const API_BASE = (import.meta.env.VITE_API_BASE_URL || '/python-api').replace(/\/$/, '')

export class ApiError extends Error {
  constructor(message: string, public readonly status: number) { super(message); this.name = 'ApiError' }
}

export async function api<T>(path: string, options: {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE'
  token?: string
  body?: unknown
  headers?: Record<string, string>
  signal?: AbortSignal
} = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: options.method || 'GET',
    headers: {
      ...(options.body === undefined ? {} : { 'Content-Type': 'application/json' }),
      ...(options.token ? { Authorization: `Bearer ${options.token}` } : {}),
      ...options.headers,
    },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: options.signal,
  })
  let payload: unknown
  try { payload = await response.json() } catch { throw new ApiError('服务暂时无法响应，请确认后端已启动', response.status) }
  if (!response.ok) {
    const detail = payload && typeof payload === 'object' && 'detail' in payload
      ? (payload as { detail: unknown }).detail : null
    throw new ApiError(typeof detail === 'string' ? detail : `请求失败（HTTP ${response.status}）`, response.status)
  }
  if (!payload || typeof payload !== 'object' || !('code' in payload)
      || (payload as { code: unknown }).code !== 0) throw new ApiError('接口响应格式不正确', response.status)
  // 后端封套恒带 data（可为 null）；序列化缺键时按 null 处理。
  return ('data' in payload ? (payload as { data: T }).data : null) as T
}
