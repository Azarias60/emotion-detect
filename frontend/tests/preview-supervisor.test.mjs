import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import ts from 'typescript'

const source = fs.readFileSync(new URL('../src/application/preview.ts', import.meta.url), 'utf8')
const output = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText
const { createPreviewSupervisor } = await import(`data:text/javascript;base64,${Buffer.from(output).toString('base64')}`)

const offline = { connected: false, frame_available: false }
const online = { connected: true, frame_available: true }

test('进入页面自动检查设备；离线显示占位，设备恢复自动预览', async () => {
  let status = offline
  let connects = 0
  let disconnects = 0
  let lost
  const changes = []
  const stream = { device: 'backend-only' }
  const supervisor = createPreviewSupervisor(
    async () => status,
    {
      connect: async callback => { connects++; lost = callback; return stream },
      disconnect: async () => { disconnects++ },
    },
    update => changes.push(update),
  )
  await supervisor.sync()
  assert.equal(connects, 0)
  assert.deepEqual(changes.at(-1), { capture: offline })
  status = online
  await supervisor.sync()
  assert.equal(connects, 1)
  assert.deepEqual(changes.at(-1), { stream })
  status = { ...online, frame_available: false }
  await supervisor.sync()
  assert.equal(disconnects, 0, 'a temporary frame gap does not replace the peer')
  status = online
  await supervisor.sync()
  assert.equal(connects, 1)
  lost()
  assert.equal(changes.at(-1).stream, null)
  await supervisor.sync()
  assert.equal(connects, 2)
  status = offline
  await supervisor.sync()
  assert.ok(disconnects >= 1)
  assert.equal(changes.at(-1).stream, null)
  await supervisor.dispose()
  const before = connects
  status = online
  await supervisor.sync()
  assert.equal(connects, before)
})

test('离开页面时晚到的状态不建立预览，且可重复释放', async () => {
  let resume
  let connects = 0
  let disconnects = 0
  const events = []
  const supervisor = createPreviewSupervisor(
    () => new Promise(resolve => { resume = resolve }),
    {
      connect: async () => { connects++; return { device: 'backend' } },
      disconnect: async () => { disconnects++ },
    },
    change => events.push(change),
  )
  const pending = supervisor.sync()
  await supervisor.dispose()
  resume(online)
  await pending
  assert.equal(connects, 0)
  assert.equal(events.length, 0)
  assert.equal(disconnects, 1)
})

test('连接失败不伪造设备数据，并允许下一轮真实重试', async () => {
  let count = 0
  const events = []
  const supervisor = createPreviewSupervisor(
    async () => online,
    {
      connect: async () => { count++; if (count === 1) throw Error('设备视频暂无可用帧'); return 'real-stream' },
      disconnect: async () => {},
    },
    e => events.push(e),
  )
  await supervisor.sync()
  assert.equal(events.at(-1).stream, null)
  assert.equal(events.find(x => x.capture)?.capture?.connected, true)
  await supervisor.sync()
  assert.equal(count, 2)
  assert.equal(events.at(-1).stream, 'real-stream')
  await supervisor.dispose()
})
