import { detectionClient } from '@/api/http'

/** 与数字人对话（后端 /companion/chat）。 */
export function chatWithCompanion(payload) {
  return detectionClient.post('/companion/chat', payload).then((body) => body.data)
}

export const companionModules = {
  health: { title: '健康问诊', path: '/detect/health', desc: '舌象 · 心率 · 综合分析' },
  fatigue: { title: '疲劳检测', path: '/detect/fatigue', desc: '眨眼 · 哈欠 · 疲劳评估' },
  emotion: { title: '情绪识别', path: '/detect/emotion', desc: '表情 · 语音 · 情绪状态' },
  companion: { title: '情感数字人', path: '/detect/companion', desc: '陪伴 · 对话 · 心理支持' },
}
