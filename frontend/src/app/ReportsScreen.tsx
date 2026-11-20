import { useMemo, useState, useSyncExternalStore } from 'react'
import ReportsPage, { type ReportsView } from '../pages/ReportsPage'
import type { ReportItem } from '../domain/reports'
import { emptyConditions, matchingReports, type ReportConditions } from '../domain/reports'
import type { ReportStore } from '../application/reports'
import { useLatestQuery } from './useLatestQuery'

const noSubscription = () => () => {}
const emptySnapshot = { items: [], revision: 0, synced: false }

/** 报告集合经观察事实派生；详情由 URL id 决定，删除命令只执行删除。 */
export default function ReportsScreen({ store, requestedId, onSelect }: {
  store: ReportStore | null
  requestedId: number | null
  onSelect: (id: number | null) => void
}) {
  const snapshot = useSyncExternalStore(
    store?.subscribe ?? noSubscription,
    store?.getSnapshot ?? (() => emptySnapshot),
  )
  const [applied, setApplied] = useState<ReportConditions>(emptyConditions)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState('')

  const [detailSourceTick, setDetailSourceTick] = useState(0)
  const detailSource = useMemo(() => store && requestedId && requestedId > 0
    ? () => store.detail(requestedId) : null, [store, requestedId, detailSourceTick])
  const detail = useLatestQuery(detailSource)

  async function remove(id: number) {
    if (!store || deleting) return
    setDeleting(true)
    setDeleteError('')
    try {
      await store.remove(id)
      if (requestedId === id) onSelect(null)
    } catch (error) {
      setDeleteError(error instanceof Error ? error.message : '删除失败')
    } finally {
      setDeleting(false)
    }
  }

  const matching = useMemo(
    () => matchingReports(snapshot.items, applied),
    [snapshot, applied],
  )
  const selected: ReportItem | null = detail.value
    ?? snapshot.items.find(item => item.id === requestedId) ?? null

  const view: ReportsView = {
    items: matching,
    total: snapshot.items.length,
    synced: snapshot.synced,
    applied,
    selected,
    requestedId,
    deleting,
    error: deleteError,
  }
  return <ReportsPage view={view} actions={{
    remove,
    search: next => { setApplied(next) },
    select: id => { onSelect(id); setDetailSourceTick(tick => tick + 1) },
  }} />
}
