// Only the public HTTP boundary is replaced. React, controls and report
// projections are production code; this fixture never implements filtering.
export const report = (id, type, text, answers = []) => ({
  id, type, createUserId: 42, createTime: `2026-11-${String(id).padStart(2, '0')}T04:00:00Z`,
  result: { sealed: true, report: { text }, answers },
})

export async function backend(page, initial = []) {
  const state = { reports: initial, calls: [] }
  // 观察流是公共 HTTP 边界的一部分。Playwright 的 route.fulfill 会整体
  // 缓冲响应体，无法渐进流式，所以这一个端点在页面上下文用 fetch shim
  // 构造真实 SSE 流：订阅保持打开，夹具状态变化时经 exposeFunction 通
  // 道推送。夹具只转发自身状态，不实现业务过滤。
  await page.exposeFunction('__acceptReports', items => {
    window.dispatchEvent(new CustomEvent('__reports-facts', { detail: items }))
  })
  await page.addInitScript(() => {
    const originalFetch = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const url = typeof input === 'string' ? input : (input && input.url) || ''
      if (!url.includes('/observations/reports')) return originalFetch(input, init)
      const encoder = new TextEncoder()
      const stream = new ReadableStream({
        start(controller) {
          const deliver = items => {
            controller.enqueue(encoder.encode('data: ' + JSON.stringify(items) + '\n\n'))
          }
          window.__deliverReports = deliver
          window.addEventListener('__reports-facts', event => deliver(event.detail))
        },
      })
      return new Response(stream, {
        headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store' },
      })
    }
  })
  state.pushReports = async () => {
    await page.evaluate(items => window.__deliverReports(items), structuredClone(state.reports))
  }
  await page.route('**/python-api/**', async route => {
    const request = route.request(), url = new URL(request.url())
    state.calls.push({ method: request.method(), path: url.pathname })
    let data
    if (url.pathname.endsWith('/auth/login')) data = { token: 'interface-test', user: { id: 42, username: 'tester' } }
    else if (url.pathname.endsWith('/auth/register')) data = { id: 42 }
    else if (url.pathname.endsWith('/capture/status')) data = { connected: false, frame_available: false, audio: { levels: [], age_ms: null } }
    else if (/\/reports\/\d+$/.test(url.pathname)) {
      const id = Number(url.pathname.split('/').at(-1))
      if (request.method() === 'DELETE') {
        state.reports = state.reports.filter(item => item.id !== id)
        void page.evaluate(items => window.__deliverReports(items), structuredClone(state.reports)).catch(() => {})
        data = null
      } else data = state.reports.find(item => item.id === id)
    } else if (url.pathname.endsWith('/reports') && request.method() === 'GET')
      data = { items: structuredClone(state.reports), total: state.reports.length }
    else if (url.pathname.endsWith('/auth/logout')) data = null
    else return route.fulfill({ status: 404, json: { detail: '本用例未提供该能力' } })
    return route.fulfill({ json: { code: 0, data } })
  })
  return state
}

export async function signIn(page) {
  await page.goto('/login')
  await page.getByLabel(/^(用户名|账号)$/).fill('tester')
  await page.getByLabel(/^密码$/).fill('sample123')
  await page.getByRole('button', { name: '登录', exact: true }).last().click()
  await page.waitForURL('**/')
}

export const rows = page => page.getByRole('table').first().getByRole('row').filter({ has: page.getByRole('cell') })
