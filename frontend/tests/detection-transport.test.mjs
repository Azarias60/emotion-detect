import test from 'node:test'
import assert from 'node:assert/strict'
import { loadModule } from './helpers/load-module.mjs'

// 测试侧只接入真实传输；不要求额外的前端控制器或报告服务。
async function transport() {
  const module = await loadModule('api/detection.ts')
  return module.createHttpDetection ? module.createHttpDetection('account-token') : module.httpDetection
}

for (const mode of ['health', 'emotion', 'fatigue']) {
  test(`${mode} 启动只向后端提交模式与身份，不在前端准备题目或舌象`, async () => {
    const original = globalThis.fetch, requests = []
    globalThis.fetch = async (url, options) => {
      requests.push({ url: String(url), options })
      return { ok: true, status: 200, json: async () => ({ code: 0, data: { reportId: 31 } }) }
    }
    try {
      const gateway = await transport()
      const started = await gateway.begin(mode)
      assert.equal(requests.length, 1, '准备和后续执行属于后端，启动适配只提交一次意图')
      assert.ok(requests[0].url.endsWith('/detections'))
      assert.equal(requests[0].options.headers.Authorization, 'Bearer account-token')
      assert.deepEqual(JSON.parse(requests[0].options.body), { kind: mode })
      assert.equal(started.reportId, 31)
    } finally { globalThis.fetch = original }
  })
}
