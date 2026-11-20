import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import ts from 'typescript'

// 业务会话策略隔离于浏览器、React 与网络实现。
const code = ts.transpileModule(
  fs.readFileSync(new URL('../src/domain/auth.ts', import.meta.url), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } },
).outputText
const { createSessionService } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`)

test('注册只创建账户，只有登录成功后写入标准会话', async () => {
  const user = { id: 1, username: 'tester' }
  let saved = null
  let revoked = ''
  const store = { read: () => saved, write: value => { saved = value } }
  const service = createSessionService({
    login: async () => ({ token: 'real-token', user }),
    register: async () => ({ id: user.id }),
    logout: async token => { revoked = token },
  }, store)

  assert.deepEqual(await service.register('tester', 'secret', 'secret'), { id: user.id })
  assert.equal(service.current(), null, '注册不能绕过正常登录')
  const session = await service.login('tester', 'secret')
  assert.deepEqual(session, { token: 'real-token', user })
  assert.deepEqual(service.current(), session)
  await service.logout(session)
  assert.equal(revoked, 'real-token')
  assert.equal(service.current(), null)
})

test('失败登录不制造认证态；退出 API 故障仍清除本地会话', async () => {
  let saved = null
  const service = createSessionService({
    login: async (username) => {
      if (username === 'invalid') throw new Error('用户名或密码错误')
      return { token: 'live', user: { username: 'tester' } }
    },
    register: async () => ({ id: 1 }),
    logout: async () => { throw new Error('offline') },
  }, { read: () => saved, write: value => { saved = value } })
  await assert.rejects(service.login('invalid', 'secret'), /密码错误/)
  assert.equal(service.current(), null)
  const session = await service.login('tester', 'secret')
  await assert.rejects(service.logout(session), /offline/)
  assert.equal(service.current(), null)
})

test('认证端口和登录界面不允许任何免认证旁路', () => {
  const api = fs.readFileSync(new URL('../src/api/auth.ts', import.meta.url), 'utf8')
  const domain = fs.readFileSync(new URL('../src/domain/auth.ts', import.meta.url), 'utf8')
  const screen = fs.readFileSync(new URL('../src/app/AuthScreen.tsx', import.meta.url), 'utf8')
  const page = fs.readFileSync(new URL('../src/pages/LoginPage.tsx', import.meta.url), 'utf8')
  assert.doesNotMatch(api, /\/auth\/guest|\bguest\s*:/i)
  assert.doesNotMatch(domain, /\bguest\s*\(/i)
  assert.doesNotMatch(screen, /onGuest|\.guest\s*\(/)
  assert.doesNotMatch(page, /onGuest|login-guest-link|游客/)
})
