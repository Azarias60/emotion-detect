/** 检测域：模式与传输契约。检测事实经报告能力提供，前端不编排准备、计时或保存。 */
export const modes = [
  { id: 'health', title: '健康检测' },
  { id: 'fatigue', title: '疲劳检测' },
  { id: 'emotion', title: '情绪检测' },
] as const
export type DetectionMode = (typeof modes)[number]['id']
export const isMode = (value: string | null): value is DetectionMode => modes.some(item => item.id === value)

export type TongueResult = {
  quality?: { passed: boolean; warnings: string[] } | null
  recognition?: Partial<Record<'color' | 'coated' | 'crack' | 'indentation', { label: string }>> | null
  constitution?: { candidates: { name: string }[] } | null
  constitution_report?: unknown
}

/** 一次执行绑定的检测事实（报告 result）；阶段与问答由后端过程提供。 */
export type DetectionResult = {
  stage?: 'measuring' | 'analyzing' | 'complete' | 'failed'
  failure?: string
  collecting_since?: string | null
  questions?: string[]
  answers?: { question: string; answer: string }[]
  transcript?: { index: number; text: string } | null
  tongue?: TongueResult | null
  [key: string]: unknown
}

export type QuestionProgress = {
  index: number
  question: string
  total: number
}

export type RoundStage = 'preparing' | 'collecting' | 'measuring' | 'analyzing' | 'complete' | 'failed'

/** 本轮报告的派生视图：阶段、倒计时、当前题与转写都从报告事实得出。 */
export type RoundView = {
  id: number
  stage: RoundStage
  failure: string
  remaining: number
  question: QuestionProgress | null
  transcript: string
  result: DetectionResult
}

/** 意图传输：启动只提交模式与身份，后续意图以报告标识为目标。 */
export interface DetectionGateway {
  begin(mode: DetectionMode): Promise<{ reportId: number }>
  finish(reportId: number): Promise<void>
  cancel(reportId: number): Promise<void>
  retry(reportId: number): Promise<void>
  expressUsage(reportId: number, mode: DetectionMode | null): Promise<void>
  questionDisplayed(reportId: number, index: number): Promise<void>
}
