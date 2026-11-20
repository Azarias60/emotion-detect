import { useEffect, useState } from 'react'

type QueryState<T> = {
  source: (() => Promise<T>) | null
  value: T | null
  error: string
  loading: boolean
}

/** 统一异步读取的单一状态来源：仅最新请求可提交结果。
 * 用例端口无需依赖 DOM / fetch / AbortSignal；卸载与切换参数后自动丢弃旧响应。
 */
export function useLatestQuery<T>(source: (() => Promise<T>) | null) {
  const [settled, setSettled] = useState<QueryState<T>>({
    source: null, value: null, error: '', loading: false,
  })

  useEffect(() => {
    if (!source) return
    let active = true
    let pending = false
    const read = () => {
      if (pending) return
      pending = true
      setSettled(previous => ({ source, value: previous.source === source ? previous.value : null, error: '', loading: true }))
      void Promise.resolve().then(source)
        .then(value => { if (active) setSettled({ source, value, error: '', loading: false }) })
        .catch(error => { if (active) setSettled({ source, value: null, loading: false,
          error: error instanceof Error ? error.message : '数据读取失败' }) })
        .finally(() => { pending = false })
    }
    const visible = () => { if (document.visibilityState === 'visible') read() }
    read()
    window.addEventListener('focus', read)
    document.addEventListener('visibilitychange', visible)
    return () => {
      active = false
      window.removeEventListener('focus', read)
      document.removeEventListener('visibilitychange', visible)
    }
  }, [source])

  // 请求来源变化的第一次渲染就清空过期结果，避免跨账号/报告闪现旧数据。
  if (settled.source !== source) {
    return { value: null as T | null, error: '', loading: !!source }
  }
  return { value: settled.value, error: settled.error, loading: settled.loading }
}
