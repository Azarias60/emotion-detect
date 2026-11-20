import { useEffect, useRef, useState } from 'react'
import { Alert, App as AntApp, Button, Descriptions, Empty, Modal, Progress, Spin, Timeline, Typography } from 'antd'
import { HeartFilled, PlayCircleFilled } from '@ant-design/icons'
import type { DetectionMode, DetectionResult, QuestionProgress, RoundView } from '../domain/detection'
import type { CaptureStatus } from '../domain/device'
import { physiologyMetrics, physiologyNotices, specialtyMetrics, reportText, tongueWarning } from '../domain/detectionResults'
import { MarkdownContent } from '../components/MarkdownContent'
import { SectionHeading } from '../components/WorkSurface'

export type DetectionView = {
  mode: DetectionMode
  capture: CaptureStatus | null
  stream: MediaStream | null
  otherCapture: boolean
  round: RoundView | null
  busy: boolean
  message: string
}

export type DetectionActions = {
  begin: () => void
  finish: () => void
  cancel: () => void
  retry: () => void
}
type Props = { view: DetectionView; actions: DetectionActions }
const captureReady = (view: DetectionView) => !!view.capture?.connected && !!view.capture.frame_available

function DeviceVideo({ stream }: { stream: MediaStream | null }) {
  const ref = useRef<HTMLVideoElement>(null)
  useEffect(() => {
    const video = ref.current
    if (!video) return
    video.srcObject = stream
    if (stream) void video.play().catch(() => {})
    return () => { video.srcObject = null }
  }, [stream])
  return <video ref={ref} autoPlay muted playsInline aria-label="采集设备实时画面"
    hidden={!stream} />
}

function CaptureStatus({ view }: { view: DetectionView }) {
  const stage = view.round?.stage
  const status = stage === 'collecting' ? `采集中 · 剩余 ${view.round?.remaining ?? 60} 秒`
    : stage === 'measuring' ? '正在测量'
    : stage === 'analyzing' ? '正在生成报告'
    : stage === 'preparing' ? (view.mode === 'health' ? '正在分析舌象' : '正在准备检测')
    : stage === 'failed' ? '分析未完成'
    : stage === 'complete' ? null
    : view.otherCapture ? '其他模式正在使用设备' : null
  return status ? <Typography.Text type="secondary" className="detect-capture-status">{status}</Typography.Text> : null
}

function Capture({ view, actions }: Props) {
  const { modal } = AntApp.useApp()
  const stage = view.round?.stage
  const recording = stage === 'collecting'
  const idle = !view.round || stage === 'complete'
  const ready = captureReady(view) && !view.otherCapture
  const message = view.message || (stage === 'failed' ? view.round?.failure : '')
  function confirmFinish() {
    modal.confirm({
      title: '提前结束采集',
      content: '采集尚未完成。提前结束将分析已采集的数据，信号不足 30 秒时可能缺少呼吸率和 HRV。',
      okText: '确认结束并分析', cancelText: '取消',
      onOk: actions.finish,
    })
  }
  return <div className="detect-capture">
    <div className="media-stage">
      <DeviceVideo stream={view.stream} />
      {!view.stream && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={captureReady(view) ? '等待采集画面' : '采集设备未连接'} />}
    </div>
    {view.round?.question && <Question progress={view.round.question} transcript={view.round.transcript ?? ''} />}
    <div className="detect-capture-footer">
      {recording && <Progress percent={Math.round((1 - (view.round?.remaining ?? 60) / 60) * 100)}
        size="small" showInfo={false} aria-label="采集进度" />}
      {message && <Alert type={stage === 'failed' ? 'warning' : 'info'} showIcon message={message} role="status" />}
      {(stage === 'measuring' || stage === 'analyzing') && <Spin aria-label="正在分析本轮检测" />}
      {stage !== 'measuring' && stage !== 'analyzing' && <div className="detect-actionbar">
        {idle && <Button type="primary" icon={<PlayCircleFilled />} disabled={view.busy || !ready} onClick={actions.begin}>
          {view.mode === 'health' ? '开始一分钟问诊' : '开始检测'}
        </Button>}
        {recording && <>
          <Button type="primary" disabled={view.busy} onClick={confirmFinish}>提前结束并分析</Button>
          <Button disabled={view.busy} onClick={actions.cancel}>取消本轮</Button>
        </>}
        {stage === 'failed' && <>
          <Button type="primary" disabled={view.busy} onClick={actions.retry}>重试本轮分析</Button>
          <Button onClick={actions.cancel}>取消本轮</Button>
        </>}
        {stage === 'preparing' && <Button onClick={actions.cancel}>取消本轮</Button>}
      </div>}
    </div>
  </div>
}

function Metrics({ view, onPhysiology }: { view: DetectionView; onPhysiology: () => void }) {
  const title = { health: '舌象识别', fatigue: '疲劳特征', emotion: '情绪特征' }[view.mode]
  const result = view.round?.result ?? null
  const tongue = result?.tongue ?? null
  const warning = tongue ? tongueWarning(tongue) : ''
  return <section className="workbench-stack" id="detect-metrics-panel">
    <SectionHeading title={title} />
    {view.mode === 'health' && warning && <Alert type="info" showIcon message={warning} role="status" />}
    <div className="metric-list">
      {specialtyMetrics(view.mode, result, tongue).map(item => (
        <div className="metric-row" key={item.label}>
          <span>{item.label}</span>
          <strong>{item.value == null ? '—' : `${item.value}${item.unit ?? ''}`}</strong>
        </div>
      ))}
    </div>
    <Button type="link" className="inline-action" icon={<HeartFilled aria-hidden="true" />} onClick={onPhysiology}>生理窗口</Button>
  </section>
}

function Question({ progress, transcript }: {
  progress: QuestionProgress; transcript: string
}) {
  return <div className="detect-question" aria-live="polite">
    <Typography.Text type="secondary">第 {progress.index + 1}/{progress.total} 题</Typography.Text>
    <Typography.Paragraph strong style={{ marginBottom: 0 }}>
      {progress.question.replace(/^\s*\d+[.、．]\s*/, '')}
    </Typography.Paragraph>
    {transcript && <Typography.Text type="secondary" className="detect-transcript">{transcript}</Typography.Text>}
  </div>
}

const STEP_INDEX = { preparing: 1, collecting: 2, measuring: 3, analyzing: 3, failed: 3, complete: 4 }

function ReportPreview({ view, onDetail, onAnswers }: {
  view: DetectionView; onDetail: () => void; onAnswers: () => void
}) {
  const stage = view.round?.stage
  const completed = stage === 'complete'
  const text = reportText(view.round?.result ?? null)
  const answers = (view.round?.result?.answers ?? []) as { question: string; answer: string }[]
  const step = (stage && STEP_INDEX[stage]) || 0
  return <div className="detect-report" id="detect-report-panel">
    <section className="workbench-stack">
      <SectionHeading title="报告预览">
        {completed && <Button type="link" size="small" onClick={onDetail}>查看完整报告</Button>}
      </SectionHeading>
      {!completed && <>
        <Typography.Text>{stage === 'measuring' ? '正在测量检测指标'
          : stage === 'analyzing' ? '正在生成报告'
          : stage === 'failed' ? '可重试本轮分析'
          : stage === 'preparing' ? '正在准备本轮检测' : '等待检测'}</Typography.Text>
        <Timeline items={['检测准备', '60 秒采集', '自动分析', '生成报告'].map((title, index) => ({
          color: index < step ? 'green' : 'gray', content: title,
        }))} />
      </>}
      {completed && <div className="detect-report-copy">
        <MarkdownContent source={text || '本轮未返回报告文本'} />
      </div>}
    </section>
    {view.mode !== 'fatigue' && <section className="workbench-stack">
      <SectionHeading title="逐题记录">
        {completed && answers.length > 0 && <Button type="link" size="small" onClick={onAnswers}>完整问答</Button>}
      </SectionHeading>
      {answers.length === 0
        ? <Typography.Text type="secondary">本轮尚无已完成问答</Typography.Text>
        : <Timeline items={answers.slice(0, 4).map(answer => ({
            content: <Typography.Text><strong>{answer.question}</strong>：{answer.answer || '（未识别到回答）'}</Typography.Text>,
          }))} />}
    </section>}
  </div>
}

function PhysiologyModal({ view, open, onClose }: { view: DetectionView; open: boolean; onClose: () => void }) {
  const result = view.round?.result ?? null
  const measurement = result?.physiology
  return <Modal open={open} onCancel={onClose} footer={null} title="生理窗口" width={560} destroyOnHidden>
    {measurement
      ? <>
        <Descriptions column={1} size="small" items={physiologyMetrics(result).map(item => ({
          key: item.label, label: `${item.label}${item.unit ? `（${item.unit}）` : ''}`,
          children: item.value == null ? '—' : String(item.value),
        }))} />
        {physiologyNotices(result).map(notice => <Alert key={notice} type="info" showIcon message={notice} />)}
      </>
      : <Empty description={view.round?.stage === 'measuring' || view.round?.stage === 'analyzing'
        ? '正在测量本轮生理指标' : '暂无本轮检测结果'} />}
  </Modal>
}

function DetailModal({ view, open, onClose }: { view: DetectionView; open: boolean; onClose: () => void }) {
  const text = reportText(view.round?.result ?? null)
  return <Modal open={open} onCancel={onClose} footer={null} title="本轮完整报告" width={720} destroyOnHidden>
    <MarkdownContent source={text || '本轮未返回报告文本'} />
  </Modal>
}

function AnswersModal({ view, open, onClose }: { view: DetectionView; open: boolean; onClose: () => void }) {
  const answers = (view.round?.result?.answers ?? []) as { question: string; answer: string }[]
  return <Modal open={open} onCancel={onClose} footer={null} title="完整逐题问答" width={640} destroyOnHidden>
    {answers.length === 0 ? <Empty description="本轮没有已结清问答" />
      : <Timeline items={answers.map(answer => ({
          children: <Typography.Text><strong>{answer.question}</strong>：{answer.answer || '（未识别到回答）'}</Typography.Text>,
        }))} />}
  </Modal>
}

export function PhysiologyPage({ result, backHref }: {
  result: DetectionResult | null; backHref: string
}) {
  const measurement = result?.physiology
  return <section className="workbench-stack" id="physiology">
    <SectionHeading title="生理窗口" />
      {measurement ? <>
        <Descriptions column={1} size="small" items={physiologyMetrics(result).map(item => ({
          key: item.label, label: `${item.label}${item.unit ? `（${item.unit}）` : ''}`,
          children: item.value == null ? '—' : String(item.value),
        }))} />
        {physiologyNotices(result).map(notice => <Alert key={notice} type="info" showIcon message={notice} />)}
      </> : <Empty description={result?.stage === 'measuring' || result?.stage === 'analyzing'
        ? '正在测量本轮生理指标' : '暂无本轮检测结果'} />}
      <Button type="link" href={backHref}>返回检测指标</Button>
  </section>
}

export default function DetectionPage(props: Props) {
  const { view } = props
  const [physiologyOpen, setPhysiologyOpen] = useState(false)
  const [detailOpen, setDetailOpen] = useState(false)
  const [answersOpen, setAnswersOpen] = useState(false)
  return <>
    <div className="workbench-grid detect-layout">
      <Metrics view={view} onPhysiology={() => setPhysiologyOpen(true)} />
      <section className="workbench-stack" id="detect-capture-panel">
        <SectionHeading title="采集画面"><CaptureStatus view={view} /></SectionHeading>
        <Capture {...props} />
      </section>
      <ReportPreview view={view} onDetail={() => setDetailOpen(true)} onAnswers={() => setAnswersOpen(true)} />
    </div>
    <PhysiologyModal view={view} open={physiologyOpen} onClose={() => setPhysiologyOpen(false)} />
    <DetailModal view={view} open={detailOpen} onClose={() => setDetailOpen(false)} />
    <AnswersModal view={view} open={answersOpen} onClose={() => setAnswersOpen(false)} />
  </>
}
