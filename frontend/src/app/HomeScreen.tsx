import { useEffect } from 'react'
import { GuideBoard } from '../components/GuideBoard'
import { DIGITAL_HUMAN_PATH, detectionHref } from './routes'
import { useGuideSession } from './useGuideSession'
import type { GuideTransport } from '../domain/guideSession'

const entryLinks = {
  health: detectionHref('health'),
  fatigue: detectionHref('fatigue'),
  emotion: detectionHref('emotion'),
  companion: DIGITAL_HUMAN_PATH,
}

/** 首页导览：语音对话（唤醒「你好」+ 自动开麦）、资料总结卡、模块推荐确认跳转，
    全部来自数字人引擎（WS default 模式 + PCM 回放）；5174 同款三栏控制台。*/
export default function HomeScreen({ gateways, active, guideTransport, onNavigate }: {
  gateways: {
    conversation: {
      expressUsage(kind: 'guide' | 'companion', active: boolean): Promise<void>
      send(kind: 'guide' | 'companion', text: string): Promise<{ reply: string }>
      restart(kind: 'guide' | 'companion'): Promise<void>
    }
  }
  active: boolean
  guideTransport: GuideTransport
  onNavigate: (to: string) => void
}) {
  const guide = useGuideSession(guideTransport, module => {
    const target = (entryLinks as Record<string, string>)[module] ?? (module === 'companion' ? DIGITAL_HUMAN_PATH : '')
    if (target) onNavigate(target)
  })

  // 首页使用期：进入绑定导览会话使用条件，离开结束（导览上下文不跨页保留）。
  useEffect(() => {
    if (!active) return
    void gateways.conversation.expressUsage('guide', true).catch(() => {})
  }, [gateways, active])

  return <>
    <div className="home home-board">
      <h1 className="sr-only">PhysioSense 未来健康控制中心</h1>
      <GuideBoard guide={guide} />
    </div>
    <span hidden>{entryLinks.health}</span>
  </>
}
