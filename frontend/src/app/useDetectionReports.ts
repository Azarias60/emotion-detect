import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import type { DetectionGateway, DetectionMode, DetectionResult, QuestionProgress, RoundStage, RoundView } from '../domain/detection'
import { selectActiveRound, selectCurrentReport, type ReportStore } from '../application/reports'

const FINISHED = new Set(['complete', 'failed'])
const COLLECTION_MS = 60_000

function roundStage(result: DetectionResult): RoundStage {
  if (result.stage) return result.stage
  // 封存前的阶段由事实派生：题目已准备即进入采集，否则仍在准备。
  return result.questions?.length ? 'collecting' : 'preparing'
}

function collectionRemaining(result: DetectionResult, now: number): number {
  if (!result.collecting_since) return COLLECTION_MS / 1000
  const started = Date.parse(result.collecting_since)
  if (Number.isNaN(started)) return COLLECTION_MS / 1000
  return Math.max(0, Math.ceil((COLLECTION_MS - (now - started)) / 1000))
}

function currentQuestion(result: DetectionResult): QuestionProgress | null {
  const questions = result.questions ?? []
  const answers = result.answers ?? []
  if (answers.length >= questions.length) return null
  return { index: answers.length, question: questions[answers.length], total: questions.length }
}

/** 检测页面的使用条件：进入/换模式时绑定本轮，离开或换模式时结束上一条件。

绑定只表达页面使用条件；取消与移除由后端按真实采集状态裁决。
*/
function useUsageBinding(
  gateway: DetectionGateway,
  items: ReturnType<ReportStore['getSnapshot']>['items'],
  mode: DetectionMode,
) {
  const bound = useRef<{ reportId: number; mode: DetectionMode } | null>(null)
  useEffect(() => {
    const round = selectActiveRound(items, mode)
    const current = bound.current
    if (round && (!current || current.reportId !== round.id || current.mode !== mode)) {
      if (current) void gateway.expressUsage(current.reportId, null).catch(() => {})
      bound.current = { reportId: round.id, mode }
      void gateway.expressUsage(round.id, mode).catch(() => {})
    } else if (!round && current && current.mode === mode) {
      // 本模式的轮次已完成或被移除：解除绑定（对已封存报告的结束条件会被忽略）。
      bound.current = null
      void gateway.expressUsage(current.reportId, null).catch(() => {})
    }
  }, [gateway, items, mode])
  useEffect(() => () => {
    const current = bound.current
    bound.current = null
    if (current) void gateway.expressUsage(current.reportId, null).catch(() => {})
  }, [gateway])
}

/** 题目展示反馈：当前题在页面呈现时向问答能力确认一次。 */
function useDisplayFeedback(
  gateway: DetectionGateway,
  items: ReturnType<ReportStore['getSnapshot']>['items'],
  mode: DetectionMode,
) {
  const confirmed = useRef('')
  useEffect(() => {
    const round = selectActiveRound(items, mode)
    if (!round || round.result.stage) return
    const question = currentQuestion(round.result)
    if (!question) return
    const key = `${round.id}:${question.index}`
    if (confirmed.current === key) return
    confirmed.current = key
    void gateway.questionDisplayed(round.id, question.index).catch(() => {})
  }, [gateway, items, mode])
}

/** 检测消费：从报告观察事实派生呈现与操作可用性，表达意图与使用条件。 */
export function useDetectionReports(
  store: ReportStore,
  gateway: DetectionGateway,
  mode: DetectionMode,
) {
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [now, setNow] = useState(() => Date.now())

  useUsageBinding(gateway, snapshot.items, mode)
  useDisplayFeedback(gateway, snapshot.items, mode)

  // 采集剩余秒数只服务显示；封存边界归后端时钟，不由前端计时驱动。
  const counting = useMemo(
    () => snapshot.items.some(item => !FINISHED.has(String(item.result.stage))
      && roundStage(item.result) === 'collecting'),
    [snapshot],
  )
  useEffect(() => {
    if (!counting) return
    const timer = window.setInterval(() => setNow(Date.now()), 500)
    return () => window.clearInterval(timer)
  }, [counting])

  const currentReport = useMemo(
    () => selectCurrentReport(snapshot.items, mode),
    [snapshot, mode],
  )
  const otherCapture = useMemo(
    () => snapshot.items.some(item => item.type !== mode
      && !FINISHED.has(String(item.result.stage))),
    [snapshot, mode],
  )

  const describe = (error: unknown) => error instanceof Error ? error.message : '检测操作失败'

  const act = useCallback((action: () => Promise<void>, successMessage = '') => {
    if (busy) return
    setBusy(true)
    setMessage('')
    action().then(() => { if (successMessage) setMessage(successMessage) })
      .catch(error => setMessage(describe(error)))
      .finally(() => setBusy(false))
  }, [busy])

  const round = useMemo(() => {
    if (!currentReport) return null
    const result = currentReport.result
    const stage = roundStage(result)
    return {
      id: currentReport.id,
      stage,
      failure: result.failure ?? '',
      remaining: collectionRemaining(result, now),
      question: currentQuestion(result),
      transcript: result.transcript && result.transcript.index === (result.answers ?? []).length
        ? result.transcript.text : '',
      result,
    }
  }, [currentReport, now])

  const actions = useMemo(() => ({
    begin: () => act(async () => {
      await gateway.begin(mode)
    }),
    finish: () => {
      const roundId = round?.id
      if (!roundId || round?.stage === 'complete' || round?.stage === 'failed') return
      return act(async () => { await gateway.finish(roundId) })
    },
    cancel: () => {
      const roundId = round?.id
      if (!roundId) return
      return act(async () => { await gateway.cancel(roundId) })
    },
    retry: () => {
      const reportId = currentReport?.id
      if (!reportId) return
      return act(async () => { await gateway.retry(reportId) })
    },
  }), [act, gateway, mode, round, currentReport])

  return { items: snapshot.items, synced: snapshot.synced, round, otherCapture, busy, message, actions }
}
