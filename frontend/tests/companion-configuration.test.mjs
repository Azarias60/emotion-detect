import test from 'node:test'
import assert from 'node:assert/strict'
import { loadModule, requiredFunction } from './helpers/load-module.mjs'

const resolve = async () => requiredFunction(await loadModule('domain/digitalHuman.ts'), 'resolveCompanion')
const data = overrides => ({
  configs: [{ id: 'c1', name: '陪伴', avatar: 'a1', voice: 'v1' }],
  avatars: [{ id: 'a1' }, { id: 'a2' }],
  voices: [{ id: 'v1' }, { id: 'v2' }],
  selectedConfigId: 'c1', temporaryVoiceId: '', ...overrides,
})

test('当前形象和默认音色从所选配置及账号资源集合派生', async () => {
  const resolveCompanion = await resolve(), input = data()
  const value = resolveCompanion(input)
  assert.deepEqual(value.configuration, input.configs[0])
  assert.deepEqual(value.avatar, input.avatars[0])
  assert.deepEqual(value.voice, input.voices[0])
})

test('会话临时音色优先于默认音色，选择不修改永久配置', async () => {
  const resolveCompanion = await resolve(), input = data({ temporaryVoiceId: 'v2' })
  const before = structuredClone(input)
  assert.equal(resolveCompanion(input).voice.id, 'v2')
  assert.deepEqual(input, before)
})

test('形象引用缺失时解析为不可用，不改写引用或自动替换对象', async () => {
  const resolveCompanion = await resolve(), input = data({ avatars: [{ id: 'a2' }] })
  const value = resolveCompanion(input)
  assert.equal(value.configuration.avatar, 'a1')
  assert.equal(value.avatar, null)
  assert.equal(value.voice.id, 'v1')
})

test('所选临时音色消失后呈现缺失，不把默认音色伪装成原选择', async () => {
  const resolveCompanion = await resolve(), input = data({ temporaryVoiceId: 'v2', voices: [{ id: 'v1' }] })
  const value = resolveCompanion(input)
  assert.equal(value.configuration.voice, 'v1')
  assert.equal(value.avatar.id, 'a1')
  assert.equal(value.voice, null)
})
