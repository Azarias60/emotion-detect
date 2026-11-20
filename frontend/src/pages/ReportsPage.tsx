import { useEffect, useState } from 'react'
import { Alert, Button, DatePicker, Descriptions, Empty, Form, Input, Modal, Popconfirm, Select, Spin, Table, Timeline, Typography } from 'antd'
import dayjs, { type Dayjs } from 'dayjs'
import type { ReportConditions, ReportItem } from '../domain/reports'
import {
  reportContent, reportSummary, reportTitle, reportTypeName, reportTypeNames,
  summarizeReports,
} from '../domain/reports'
import { jsonMarkdown, MarkdownContent } from '../components/MarkdownContent'
import { SectionHeading } from '../components/WorkSurface'

export type ReportsView = {
  items: ReportItem[]
  total: number
  synced: boolean
  applied: ReportConditions
  selected: ReportItem | null
  requestedId: number | null
  deleting: boolean
  error: string
}
export type ReportsActions = {
  search: (conditions: ReportConditions) => void
  select: (id: number) => void
  remove: (id: number) => void
}

const kindOptions = (Object.keys(reportTypeNames) as (keyof typeof reportTypeNames)[])
  .map(value => ({ value, label: reportTypeNames[value] }))

function ReportFilters({ view, actions }: { view: ReportsView; actions: ReportsActions }) {
  const [form] = Form.useForm()
  useEffect(() => {
    form.setFieldsValue({
      type: view.applied.type || undefined,
      keyword: view.applied.keyword,
      range: view.applied.startDate || view.applied.endDate
        ? [dayjs(view.applied.startDate), dayjs(view.applied.endDate)] as [Dayjs, Dayjs]
        : undefined,
    })
  }, [form, view.applied])
  function submit() {
    const { type, keyword, range } = form.getFieldsValue()
    actions.search({
      type: (type ?? '') as ReportConditions['type'],
      keyword: keyword ?? '',
      startDate: range?.[0]?.format('YYYY-MM-DD') ?? '',
      endDate: range?.[1]?.format('YYYY-MM-DD') ?? '',
    })
  }
  return <section id="reports-filters" className="workbench-stack">
    <SectionHeading title="筛选与检索" />
    <Form form={form} layout="vertical" onFinish={submit} className="work-filter-form">
      <Form.Item name="type" label="报告类型"><Select allowClear placeholder="全部类型"
        options={kindOptions} /></Form.Item>
      <Form.Item name="keyword" label="关键词">
        <Input allowClear placeholder="问答、建议或解读中的文字" />
      </Form.Item>
      <Form.Item name="range" label="日期范围"><DatePicker.RangePicker /></Form.Item>
      <Form.Item><Button type="primary" htmlType="submit">查询</Button></Form.Item>
    </Form>
  </section>
}

function ReportAnswersModal({ item, onClose }: { item: ReportItem; onClose: () => void }) {
  const answers = (item.result.answers ?? []) as { question: string; answer: string }[]
  return <Modal open onCancel={onClose} footer={null}
    title={`逐题问答 · ${reportTitle(item.type)} #${item.id}`} width={640} destroyOnHidden>
    {answers.length === 0 ? <Empty description="本轮没有已结清问答" />
      : <Timeline items={answers.map(answer => ({
          children: <Typography.Text><strong>{answer.question}</strong>：{answer.answer || '（未识别到回答）'}</Typography.Text>,
        }))} />}
  </Modal>
}

function ReportDetailModal({ view, onClose }: { view: ReportsView; onClose: () => void }) {
  const item = view.selected
  const answers = (item?.result.answers ?? []) as { question: string; answer: string }[]
  const body = item ? (() => {
    const content = reportContent(item)
    return content.kind === 'json' ? jsonMarkdown(content.data) : content.text
  })() : ''
  return <Modal open={item !== null} onCancel={onClose} footer={null}
    title={item ? `${reportTitle(item.type)} · #${item.id}` : '报告详情'}
    width={720} destroyOnHidden>
    {item && <>
      <Typography.Text type="secondary">
        {reportTypeName(item.type)} · 创建时间 {item.createTime}
      </Typography.Text>
      {answers.length > 0 && <>
        <Typography.Title level={5}>逐题问答</Typography.Title>
        <Timeline items={answers.map(answer => ({
          children: <Typography.Text><strong>{answer.question}</strong>：{answer.answer || '（未识别到回答）'}</Typography.Text>,
        }))} />
      </>}
      <Typography.Title level={5}>报告内容</Typography.Title>
      <div className="report-detail-text"><MarkdownContent source={body || '本轮未返回报告文本'} /></div>
    </>}
  </Modal>
}

function ComprehensiveModal({ items, onClose }: { items: ReportItem[]; onClose: () => void }) {
  const [summary] = useState(() => summarizeReports(items))
  return <Modal open onCancel={onClose} footer={null} title="综合报告（临时查看）" width={720} destroyOnHidden>
    <Descriptions column={1} size="small" items={[
      { key: 'count', label: '报告数量', children: String(summary.reportCount) },
      { key: 'types', label: '类型构成', children: Object.entries(summary.typeCounts)
        .map(([type, count]) => `${reportTypeNames[type as keyof typeof reportTypeNames]} ${count} 次`).join('、') || '无' },
      { key: 'scope', label: '汇总范围', children: '本次打开时匹配的全部报告快照；结果不保存为历史报告' },
    ]} />
    <div className="report-detail-text"><MarkdownContent source={summary.analysis} /></div>
  </Modal>
}

export default function ReportsPage({ view, actions }: { view: ReportsView; actions: ReportsActions }) {
  const [detailOpen, setDetailOpen] = useState(false)
  const [summaryOpen, setSummaryOpen] = useState(false)
  const [answerItem, setAnswerItem] = useState<ReportItem | null>(null)
  useEffect(() => { if (view.selected) setDetailOpen(true) }, [view.selected])
  useEffect(() => { if (!view.synced) setSummaryOpen(false) }, [view.synced])

  const columns: { title: string; dataIndex?: keyof ReportItem; key: string; width?: number; ellipsis?: boolean; render?: (value: unknown, item: ReportItem) => React.ReactNode }[] = [
    { title: '报告类型', dataIndex: 'type', key: 'type', width: 120,
      render: value => reportTypeName(String(value)) },
    { title: '报告信息', key: 'info', ellipsis: true,
      render: (_, item) => <div>
        <strong>{reportTitle(item.type)}</strong>
        <Typography.Paragraph type="secondary" ellipsis={{ rows: 2 }} style={{ marginBottom: 0 }}>
          {reportSummary(item) || '暂无可展示文字'}
        </Typography.Paragraph>
      </div> },
    { title: '创建时间', dataIndex: 'createTime', key: 'createTime', width: 200 },
    { title: '操作', key: 'actions', width: 220,
      render: (_, item) => <>
        <Button type="link" size="small" onClick={() => { actions.select(item.id); setDetailOpen(true) }}
          aria-label={`查看报告 ${item.id}`}>查看</Button>
        {item.type !== 'fatigue' && <Button type="link" size="small"
          onClick={() => setAnswerItem(item)} aria-label={`逐题问答 ${item.id}`}>逐题问答</Button>}
        <Popconfirm title="确认永久删除这份报告？"
          onConfirm={() => actions.remove(item.id)}>
          <Button type="link" size="small" danger loading={view.deleting}>删除</Button>
        </Popconfirm>
      </> },
  ]

  return <div className="reports-page">
    <ReportFilters view={view} actions={actions} />
    <section id="reports-list" className="workbench-stack report-list-panel">
      <SectionHeading title="历史报告">
        <Button disabled={!view.synced} onClick={() => setSummaryOpen(true)}>综合报告</Button>
      </SectionHeading>
      {view.error && <Alert type="error" showIcon title={view.error} />}
      {!view.synced && view.items.length === 0
        ? <Spin tip="正在同步报告…"><Empty description={false} style={{ padding: 48 }} /></Spin>
        : view.items.length === 0
          ? <Empty description="暂无匹配报告；完成检测后，报告会出现在这里。" />
          : <Table<ReportItem> rowKey="id" columns={columns} dataSource={view.items} scroll={{ x: 680 }}
              pagination={{ showTotal: total => `共 ${total} 份 · 匹配 ${view.items.length} 份展示` }}
              rowClassName={item => item.id === view.requestedId ? 'ant-table-row-selected' : ''} />}
    </section>
    {detailOpen && <ReportDetailModal view={view} onClose={() => setDetailOpen(false)} />}
    {answerItem && <ReportAnswersModal item={answerItem} onClose={() => setAnswerItem(null)} />}
    {summaryOpen && <ComprehensiveModal items={view.items} onClose={() => setSummaryOpen(false)} />}
  </div>
}
