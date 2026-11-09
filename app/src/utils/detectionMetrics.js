export const hasValue = (value) => value !== null && value !== undefined && value !== ''
export const scalePercent = (value, min, max) =>
  hasValue(value) && Number.isFinite(Number(value)) && max > min
    ? Math.max(0, Math.min(100, ((Number(value) - min) / (max - min)) * 100))
    : 0
export const formatMetric = (value, digits = 1) =>
  hasValue(value) && Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : '--'

export function fatigueMetrics(result) {
  const value = result?.physiological || {}
  return [
    { label: '眨眼次数', value: value.blink_count ?? null, unit: '次', icon: '◉', max: 60 },
    { label: '眨眼频率', value: value.blink_frequency ?? null, unit: '次/分', icon: '◎', max: 40 },
    { label: '哈欠次数', value: value.yawn_count ?? null, unit: '次', icon: '◯', max: 20 },
    { label: '点头次数', value: value.nod_count ?? null, unit: '次', icon: '◆', max: 20 },
    ...[
      ['闭眼占比', 'avg_perclos', '▬'],
      ['说话占比', 'speaking_ratio', '≈'],
      ['疲劳概率', 'fatigue_probability', '▲'],
    ].map(([label, key, icon]) => ({
      label,
      value: value[key] == null ? null : (value[key] * 100).toFixed(1),
      unit: '%',
      icon,
      max: 100,
    })),
  ]
}

export function physiologicalMetrics(data = {}, live = false) {
  const hr = data.heart_rate || {}
  const temp = data.temperature || {}
  const hrv = hr.hrv_indices || {}
  const bp = hr.blood_pressure || {}
  return {
    heartRate: { value: formatMetric(live ? hr.latest_hr : hr.avg_hr), unit: 'bpm' },
    respirationRate: { value: '--', unit: '次/分' },
    hrvIndex: {
      sdnn: formatMetric(hrv.SDNN),
      rmssd: formatMetric(hrv.RMSSD),
      pnn50: formatMetric(hrv.pNN50),
      nn50: hrv.NN50 ?? '--',
    },
    bloodPressure: {
      systolic: formatMetric(bp.systolic, 0),
      diastolic: formatMetric(bp.diastolic, 0),
      status: bp.status || '待分析',
    },
    rawHeartRateList: hr.hr_list || [],
    temperature: {
      ambient: formatMetric(live ? temp.latest_ambient_temp : temp.avg_ambient_temp),
      object: formatMetric(live ? temp.latest_object_temp : temp.avg_object_temp),
      unit: '°C',
    },
  }
}

// 后端将有符号 PCM 振幅映射到 ±24，静音绘制在中心线。
export function audioWavePoints(values = []) {
  if (!Array.isArray(values)) return ''
  const samples = values.slice(-512)
  if (
    !samples.length ||
    samples.some((value) => typeof value !== 'number' || !Number.isFinite(value))
  )
    return ''
  return samples
    .map((value, index) => {
      const x = samples.length === 1 ? 64 : (index * 128) / (samples.length - 1)
      return `${x} ${25 - Math.max(-24, Math.min(24, value))}`
    })
    .join(' ')
}

// 心率历史趋势，只绘制实测数据，不模拟 ECG。
export function heartRatePoints(values = []) {
  if (!Array.isArray(values)) return ''
  const points = values
    .filter(
      (value) => typeof value === 'number' && Number.isFinite(value) && value >= 30 && value <= 230
    )
    .slice(-128)
    .map(Number)
  if (!points.length) return ''
  if (points.length === 1) return '64 25'
  const min = Math.min(...points) - 5
  const max = Math.max(...points) + 5
  return points
    .map(
      (value, index) =>
        `${(index * 127) / (points.length - 1)} ${45 - scalePercent(value, min, max) * 0.4}`
    )
    .join(' ')
}
