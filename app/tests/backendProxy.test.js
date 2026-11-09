import assert from 'node:assert/strict'
import test from 'node:test'
import { EventEmitter } from 'node:events'
import { createServer } from 'node:http'
import { once } from 'node:events'
import { configureBackendProxy } from '../config/backendProxy.js'
import { errorMessage } from '../src/api/http.js'

test('unavailable backend produces a readable 503 response through the HTTP error parser', async () => {
  const proxy = new EventEmitter()
  configureBackendProxy(proxy)
  const server = createServer((request, response) => {
    proxy.emit('error', Object.assign(new Error('connect ECONNREFUSED'), { code: 'ECONNREFUSED' }), request, response)
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  try {
    const response = await fetch(`http://127.0.0.1:${server.address().port}/hardware/mode`)
    assert.equal(response.status, 503)
    const data = await response.json()
    assert.match(errorMessage({ response: { data } }), /后端服务（8081）/)
    assert.match(data.msg, /模型加载完成/)
  } finally {
    await new Promise((resolve) => server.close(resolve))
  }
})
