import { computed } from 'vue'
import {
  formatMetric,
  physiologicalMetrics,
  heartRatePoints,
  audioWavePoints,
} from '../../utils/detectionMetrics.js'
import blink from '../../assets/blink.png'
import perclos from '../../assets/avg_perclos.png'
import yawn from '../../assets/yawn.png'
import tired from '../../assets/tired.png'
import status from '../../assets/status.png'
import happy from '../../assets/happy.png'
import angry from '../../assets/angry.png'
import sad from '../../assets/sad.png'
import anxious from '../../assets/anxious.png'

const labels = [
  ['angry', '生气', '😠'],
  ['disgust', '厌恶', '🤢'],
  ['fear', '恐惧', '😨'],
  ['happy', '开心', '😊'],
  ['neutral', '平静', '😐'],
  ['sad', '难过', '😢'],
  ['surprise', '惊讶', '😲'],
]
export function useEmotionPresentation(result, liveData) {
  const emotions = computed(() =>
    labels.map(([key, label, icon]) => ({
      key,
      label,
      icon,
      val:
        result.value?.total_frames > 0
          ? (
              ((result.value.face_emotion_stats?.[key] || 0) / result.value.total_frames) *
              100
            ).toFixed(1)
          : 0,
    }))
  )
  const emotionWordCount = computed(() =>
    [
      ['喜', happy],
      ['怒', angry],
      ['哀', sad],
      ['虑', anxious],
    ].map(([label, iconUrl]) => ({
      label,
      iconUrl,
      count: result.value?.emotion_word_count?.[label] ?? 0,
      unit: '次',
    }))
  )
  const physiologicalData = computed(() => physiologicalMetrics(liveData.value || {}, true))
  const behaviorData = computed(() => {
    const value = result.value?.physiological || {}
    return [
      { label: '眨眼次数', val: value.blink_count ?? '--', unit: '次', iconUrl: blink },
      { label: '哈欠次数', val: value.yawn_count ?? '--', unit: '次', iconUrl: yawn },
      {
        label: '闭眼占比',
        val: formatMetric(value.avg_perclos == null ? null : value.avg_perclos * 100),
        unit: '%',
        iconUrl: perclos,
      },
      {
        label: '疲劳概率',
        val: formatMetric(
          value.fatigue_probability == null ? null : value.fatigue_probability * 100
        ),
        unit: '%',
        iconUrl: tired,
      },
      {
        label: '状态判定',
        val: { fatigue: '疲劳', normal: '正常' }[value.status] || value.fatigue_level || '待分析',
        unit: '',
        iconUrl: status,
      },
    ]
  })
  const voiceWave = computed(() => audioWavePoints(liveData.value?.audio?.wave ?? []))
  // 趋势固定使用设备上报的 LD6002 BPM 历史，不混入视频推算心率。
  const heartPoints = computed(() =>
    heartRatePoints(liveData.value?.heart_rate?.live_hr_list || [])
  )
  const reportParagraphs = computed(() => (result.value?.result || '暂无分析报告').split(/\n\n+/))
  return {
    emotions,
    emotionWordCount,
    physiologicalData,
    behaviorData,
    voiceWave,
    heartPoints,
    reportParagraphs,
  }
}
