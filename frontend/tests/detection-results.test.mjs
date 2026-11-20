import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import ts from 'typescript'
const code = fs.readFileSync(new URL('../src/domain/detectionResults.ts', import.meta.url), 'utf8')
const js = ts.transpileModule(code, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const { physiologyMetrics, specialtyMetrics } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)

test('已确认的舌象、行为、表情词频与六项生理指标保持真实领域含义', () => {
  assert.deepEqual(specialtyMetrics('health', null, null).map(x => x.label), ['舌色','苔色','裂纹','齿痕','体质判定'])
  assert.deepEqual(specialtyMetrics('fatigue', null, null).map(x => x.label), ['眨眼次数','眨眼频率','哈欠次数','点头次数','闭眼占比','说话占比','疲劳指数'])
  assert.deepEqual(specialtyMetrics('emotion', null, null).map(x => x.label), ['生气','厌恶','恐惧','开心','平静','难过','惊讶','喜','怒','哀','虑'])
  assert.deepEqual(physiologyMetrics(null).map(x => x.label), ['心率','呼吸率','SDNN','RMSSD','pNN50','NN50'])
  for (const mode of ['health','fatigue','emotion']) assert.ok(specialtyMetrics(mode,null,null).every(x => x.value == null))
})

test('数值来自本轮报告，稀疏表情与百分数按契约投影，不读取硬件值', () => {
  const report = { detection_id: 'r', heart_rate: { latest_bpm: 999 }, temperature: { latest_object_temp: 999 },
    physiology: { heart_rate_bpm: 72.36, respiration_rate_per_min: null, hrv: { sdnn_ms: 20, rmssd_ms: 30, pnn50_percent: 15, nn50: 3 } },
    expression: { class_counts: [['happy', 3], ['neutral', 1]] }, emotion_word_count: { 喜: 2, 怒: 0, 哀: 0, 虑: 1 },
    fatigue: { score_fraction: .375 } }
  assert.deepEqual(physiologyMetrics(report).map(x => x.value), [72.4, null, 20, 30, 15, 3])
  const emotion = specialtyMetrics('emotion', report, null)
  assert.equal(emotion.find(x => x.label === '开心').value, 75)
  assert.equal(emotion.find(x => x.label === '生气').value, 0)
  assert.equal(emotion.find(x => x.label === '喜').value, 2)
  assert.equal(specialtyMetrics('fatigue', report, null).at(-1).value, 37.5)
})
