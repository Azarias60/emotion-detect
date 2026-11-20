/** 报告领域契约：报告是同一轮检测业务数据的唯一事实来源。

标题由类型派生、摘要由已有文字截取，均为显示端派生，不是报告字段。
匹配集合、当前报告与综合汇总都从账号范围内的事实集合派生。
*/
import type { DetectionResult } from './detection'

export type ReportKind = 'health' | 'fatigue' | 'emotion'

export const reportTypeNames: Record<ReportKind, string> = {
  health: '健康检测',
  fatigue: '疲劳检测',
  emotion: '情绪检测',
}

export type ReportItem = {
  id: number
  type: ReportKind
  createUserId: number
  createTime: string
  result: DetectionResult
}

export type ReportCollection = { items: ReportItem[] }

export type ReportConditions = {
  type: '' | ReportKind
  keyword: string
  startDate: string
  endDate: string
}

/** 实际 Bearer、URL 和 HTTP 码只存在于基础设施适配器内。 */
export interface ReportsGateway {
  list(): Promise<ReportCollection>
  detail(id: number): Promise<ReportItem>
  remove(id: number): Promise<void>
}

export const emptyConditions = (): ReportConditions => ({
  type: '', keyword: '', startDate: '', endDate: '',
})

export function reportTypeName(type: string) {
  return type in reportTypeNames ? reportTypeNames[type as ReportKind] : type
}

export function reportTitle(type: string) {
  return `${reportTypeName(type)}报告`
}

/** 报告中已有的文字内容：综合解读与疲劳建议语义不同，可同时存在。 */
export function reportText(result: DetectionResult | null | undefined) {
  const report = result?.report as { text?: string } | undefined
  const advice = result?.advice as { text?: string } | undefined
  return report?.text || advice?.text || ''
}

/** 显示摘要由已有内容截取；无内容保持缺失。 */
export function reportSummary(item: ReportItem, limit = 80) {
  const text = reportText(item.result).trim()
  return text ? text.slice(0, limit) : ''
}

function validDay(value: string) {
  if (!value) return true
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const parsed = new Date(value + 'T00:00:00Z')
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value
}

export function ensureConditions(conditions: ReportConditions) {
  if (!validDay(conditions.startDate) || !validDay(conditions.endDate))
    throw new Error('日期格式应为 YYYY-MM-DD')
  if (conditions.startDate && conditions.endDate && conditions.startDate > conditions.endDate)
    throw new Error('结束日期不能早于开始日期')
}

function localDay(createTime: string) {
  const parsed = new Date(createTime)
  if (Number.isNaN(parsed.getTime())) return ''
  const offset = parsed.getTimezoneOffset() * 60_000
  return new Date(parsed.getTime() - offset).toISOString().slice(0, 10)
}

/** 匹配集合从全部报告和已应用条件派生；关键词匹配报告已有文字内容。 */
export function matchingReports(items: ReportItem[], conditions: ReportConditions): ReportItem[] {
  const keyword = conditions.keyword.trim()
  return items.filter(item => {
    if (conditions.type && item.type !== conditions.type) return false
    if (conditions.startDate && localDay(item.createTime) < conditions.startDate) return false
    if (conditions.endDate && localDay(item.createTime) > conditions.endDate) return false
    if (keyword) {
      const answers = (item.result.answers ?? [])
        .map(answer => `${answer.question}${answer.answer}`)
        .join('\n')
      const corpus = `${reportText(item.result)}\n${answers}`
      if (!corpus.includes(keyword)) return false
    }
    return true
  })
}

export type ComprehensiveSummary = {
  reportCount: number
  typeCounts: Partial<Record<ReportKind, number>>
  analysis: string
}

const SUMMARY_EXCERPT_LIMIT = 1000

/** 临时综合汇总：输入本次查看的匹配内容快照，输出汇总的纯计算。

保留原有内容功能：报告数量、类型构成、近期报告已有文字摘录及原说明；
无可摘录文字时省略，该报告仍参与数量与构成统计。结果仅供临时查看。
*/
export function summarizeReports(items: ReportItem[]): ComprehensiveSummary {
  const counts: Partial<Record<ReportKind, number>> = {}
  for (const item of items) counts[item.type] = (counts[item.type] ?? 0) + 1
  const lines = [`共分析 **${items.length}** 份历史报告。`]
  lines.push('检测构成：' + (Object.entries(counts)
    .map(([type, count]) => `${reportTypeNames[type as ReportKind]} ${count} 次`)
    .join('、') || '无') + '。')
  const recent = [...items].sort((a, b) => b.createTime.localeCompare(a.createTime)).slice(0, 5)
  const excerpts = recent
    .map(item => ({ item, text: reportText(item.result).trim().slice(0, SUMMARY_EXCERPT_LIMIT) }))
    .filter(entry => entry.text)
  if (excerpts.length) {
    lines.push('## 近期摘要')
    for (const { item, text } of excerpts)
      lines.push(`### ${reportTypeNames[item.type]} · #${item.id}\n${text}`)
  }
  lines.push('建议结合多次检测趋势观察变化；本汇总仅供健康参考，不能替代专业诊断。')
  return { reportCount: items.length, typeCounts: counts, analysis: lines.join('\n\n') }
}

/** 报告展示内容：结构化事实保持结构化，展示换行由 Markdown 能力处理。 */
export type ReportContent = { kind: 'json'; data: object } | { kind: 'text'; text: string }

export function reportContent(item: ReportItem): ReportContent {
  const text = reportText(item.result).trim()
  if (text && (text.startsWith('{') || text.startsWith('['))) {
    try {
      const data: unknown = JSON.parse(text)
      if (data && typeof data === 'object') return { kind: 'json', data: data as object }
    } catch { /* 已有文字就是普通报告正文 */ }
  }
  if (text) return { kind: 'text', text }
  return { kind: 'json', data: item.result }
}
