import type { DetectionMode } from '../domain/detection'
import type { ReportItem, ReportsGateway } from '../domain/reports'

/** 账号范围内的报告快照；React 经 useSyncExternalStore 消费。 */
export type ReportSnapshot = {
  items: ReportItem[]
  revision: number
  synced: boolean
}

const ROUND_FINISHED = new Set(['complete', 'failed'])

export function selectCurrentReport(items: ReportItem[], mode: DetectionMode): ReportItem | null {
  // 按创建顺序选择最近一份；旧报告的后台更新不改变观察对象。
  let current: ReportItem | null = null
  for (const item of items) {
    if (item.type !== mode) continue
    if (current === null || item.id > current.id) current = item
  }
  return current
}

/** 尚未完成处理的轮次：采集或封存后的分析仍在进行。 */
export function selectActiveRound(items: ReportItem[], mode?: DetectionMode): ReportItem | null {
  let active: ReportItem | null = null
  for (const item of items) {
    if (mode !== undefined && item.type !== mode) continue
    if (ROUND_FINISHED.has(String(item.result.stage))) continue
    if (active === null || item.id > active.id) active = item
  }
  return active
}

/** 报告集合存储：观察事实到达即更新快照；命令只执行所属操作。 */
export function createReportStore(
  gateway: ReportsGateway,
  connectStream: (onFacts: (items: ReportItem[]) => void) => () => void,
) {
  let snapshot: ReportSnapshot = { items: [], revision: 0, synced: false }
  const observers = new Set<() => void>()
  const changed = (synced: boolean) => {
    snapshot = { ...snapshot, revision: snapshot.revision + 1, synced }
    observers.forEach(listener => listener())
  }

  const applyFacts = (items: ReportItem[]) => {
    snapshot = { items, revision: snapshot.revision + 1, synced: true }
    observers.forEach(listener => listener())
  }

  // 会话生存期内的观察连接：订阅建立即取得当前事实，中断自动重连。
  const stopStream = connectStream(applyFacts)
  void gateway.list().then(
    collection => applyFacts(collection.items),
    () => { /* 观察流会继续提供事实 */ },
  )

  return {
    getSnapshot: () => snapshot,
    subscribe(listener: () => void) {
      observers.add(listener)
      return () => { observers.delete(listener) }
    },
    /** 删除意图只删除报告；集合与匹配结果的变化由事实流自然到达。 */
    remove(id: number) {
      return gateway.remove(id)
    },
    detail(id: number) {
      return gateway.detail(id)
    },
    stop() {
      stopStream()
    },
  }
}

export type ReportStore = ReturnType<typeof createReportStore>
