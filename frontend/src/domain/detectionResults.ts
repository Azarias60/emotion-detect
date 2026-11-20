import type { DetectionMode, DetectionResult, TongueResult } from './detection'
import type { PulseIntervalStatus } from './device'

export type Metric = { label: string; value: number | string | null | undefined; unit?: string }
export function tongueMetrics(tongue: TongueResult | null): Metric[] {
  return [
    { label: '舌色', value: tongue?.recognition?.color?.label },
    { label: '苔色', value: tongue?.recognition?.coated?.label },
    { label: '裂纹', value: tongue?.recognition?.crack?.label },
    { label: '齿痕', value: tongue?.recognition?.indentation?.label },
    { label: '体质判定', value: tongue?.constitution?.candidates?.[0]?.name },
  ]
}
export function tongueWarning(tongue: TongueResult | null) {
  if (!tongue) return ''
  if (tongue.quality?.passed === false)
    return tongue.quality.warnings.join('。') || '舌象画面质量不足，请重新拍摄'
  return tongueMetrics(tongue).some(metric => metric.value)
    ? '' : '未取得舌象识别结果，可重新拍摄或继续问诊'
}
const rounded = (n: number | null | undefined) => n == null ? null : Number(n.toFixed(1))
export function physiologyMetrics(result: DetectionResult | null): Metric[] {
  const p = result?.physiology as {
    heart_rate_bpm?: number | null
    respiration_rate_per_min?: number | null
    hrv?: { sdnn_ms?: number | null; rmssd_ms?: number | null; pnn50_percent?: number | null; nn50?: number | null } | null
  } | undefined
  return [
    { label: '心率', value: rounded(p?.heart_rate_bpm), unit: 'bpm' },
    { label: '呼吸率', value: rounded(p?.respiration_rate_per_min), unit: '次/分' },
    { label: 'SDNN', value: rounded(p?.hrv?.sdnn_ms), unit: 'ms' },
    { label: 'RMSSD', value: rounded(p?.hrv?.rmssd_ms), unit: 'ms' },
    { label: 'pNN50', value: rounded(p?.hrv?.pnn50_percent), unit: '%' },
    { label: 'NN50', value: p?.hrv?.nn50 },
  ]
}
export function specialtyMetrics(mode: DetectionMode, result: DetectionResult | null, tongue: TongueResult | null = null): Metric[] {
  if (mode === 'health') return tongueMetrics(tongue ?? (result?.tongue as TongueResult | null | undefined) ?? null)
  if (mode === 'fatigue') {
    const b = result?.behavior as {
      blink_count?: number; blink_frequency?: number; yawn_count?: number
      nod_count?: number; perclos_percent?: number; speaking_percent?: number
    } | undefined
    const fatigue = result?.fatigue as { score_fraction?: number } | undefined
    return [
      { label: '眨眼次数', value: b?.blink_count, unit: '次' },
      { label: '眨眼频率', value: rounded(b?.blink_frequency), unit: '次/分' },
      { label: '哈欠次数', value: b?.yawn_count, unit: '次' },
      { label: '点头次数', value: b?.nod_count, unit: '次' },
      { label: '闭眼占比', value: rounded(b?.perclos_percent), unit: '%' },
      { label: '说话占比', value: rounded(b?.speaking_percent), unit: '%' },
      { label: '疲劳指数', value: rounded(fatigue?.score_fraction != null ? fatigue.score_fraction * 100 : null), unit: '%' },
    ]
  }
  const expression = result?.expression as { class_counts?: [string, number][] } | undefined
  const counts = new Map(expression?.class_counts ?? [])
  const total = [...counts.values()].reduce((sum, count) => sum + count, 0)
  const wordCount = result?.emotion_word_count as Record<string, number> | undefined
  return [
    ...([['angry', '生气'], ['disgust', '厌恶'], ['fear', '恐惧'], ['happy', '开心'],
      ['neutral', '平静'], ['sad', '难过'], ['surprise', '惊讶']] as const).map(([key, label]) => ({
        label, value: total > 0 ? rounded((counts.get(key) ?? 0) / total * 100) : null, unit: '%',
      })),
    ...['喜', '怒', '哀', '虑'].map(label => ({ label, value: wordCount?.[label], unit: '次' })),
  ]
}
export const reportText = (result: DetectionResult | null) => {
  const report = result?.report as { text?: string } | undefined
  const advice = result?.advice as { text?: string } | undefined
  return report?.text || advice?.text || ''
}

const missingReason: Record<Exclude<PulseIntervalStatus, 'available'>, string> = {
  no_video: '本轮没有可分析的视频',
  no_signal: '未提取到可用的脉搏信号',
  collecting: '有效脉搏信号不足 30 秒',
  low_signal_quality: '脉搏信号质量不足',
  unavailable: '未能从脉搏间期计算该指标',
}

export function physiologyNotices(result: DetectionResult | null) {
  const diagnostics = result?.physiology_diagnostics as {
    hrv_status?: PulseIntervalStatus
    respiration_status?: PulseIntervalStatus
    window_seconds?: number
  } | undefined
  const notice = result?.measurement_notice as string | undefined
  if (!diagnostics) return notice ? [notice] : []
  const { hrv_status: hrv, respiration_status: respiration, window_seconds: seconds } = diagnostics
  const detail = (status: Exclude<PulseIntervalStatus, 'available'>) =>
    missingReason[status] + (status === 'collecting' ? `（本轮约 ${Math.floor(seconds ?? 0)} 秒）` : '')
  if (hrv && respiration && hrv === respiration && hrv !== 'available')
    return [`呼吸率和 HRV 缺测：${detail(hrv)}`]
  const messages = []
  if (respiration && respiration !== 'available') messages.push(`呼吸率缺测：${detail(respiration)}`)
  if (hrv && hrv !== 'available') messages.push(`HRV 缺测：${detail(hrv)}`)
  return messages
}
