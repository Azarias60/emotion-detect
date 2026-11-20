import type { DigitalHumanObject, DigitalHumanSelectionView, ObjectKind } from '../domain/digitalHuman'
import { resolveCompanion } from '../domain/digitalHuman'

/** 一类账号内对象集合的观察快照；React 绑定在 app 层完成。 */
export type ObjectSnapshot = { rows: DigitalHumanObject[]; synced: boolean }

export const emptyObjectSnapshot: ObjectSnapshot = { rows: [], synced: false }

/** 对象集合端口的结构契约；具体传输由组合根注入。 */
export type ObjectCollectionPort = {
  list(kind: ObjectKind): Promise<{ items: DigitalHumanObject[] }>
  create(kind: ObjectKind, values: Record<string, unknown>): Promise<DigitalHumanObject>
  update(kind: ObjectKind, id: number, values: Record<string, unknown>): Promise<unknown>
  remove(kind: ObjectKind, id: number): Promise<unknown>
  references(kind: ObjectKind, id: number): Promise<{ references: { kind: string; id: number }[] }>
}

/** 一类对象集合：观察事实到达即更新，命令只执行所属操作。 */
export function createObjectCollection(
  gateway: ObjectCollectionPort,
  kind: ObjectKind,
  connectStream: (kind: ObjectKind, onRows: (rows: DigitalHumanObject[]) => void) => () => void,
) {
  let snapshot: ObjectSnapshot = emptyObjectSnapshot
  let revision = 0
  const observers = new Set<() => void>()
  const changed = () => observers.forEach(listener => listener())
  const applyRows = (rows: DigitalHumanObject[]) => {
    revision += 1
    snapshot = { rows, synced: true }
    changed()
  }
  const stopStream = connectStream(kind, applyRows)
  async function refresh() {
    const requestedAt = ++revision
    const collection = await gateway.list(kind)
    if (requestedAt === revision) applyRows(collection.items)
  }
  void refresh().catch(() => { /* 观察流会继续提供事实 */ })

  return {
    kind,
    refresh,
    getSnapshot: () => snapshot,
    subscribe(listener: () => void) {
      observers.add(listener)
      return () => { observers.delete(listener) }
    },
    create(values: Record<string, unknown>) {
      return gateway.create(kind, values)
    },
    update(id: number, values: Record<string, unknown>) {
      return gateway.update(kind, id, values)
    },
    /** 删除只作用于目标对象；引用查询供确认弹窗呈现实际引用者。 */
    remove(id: number) {
      return gateway.remove(kind, id)
    },
    references(id: number) {
      return gateway.references(kind, id)
    },
    stop() {
      revision += 1
      stopStream()
    },
  }
}

export type ObjectCollection = ReturnType<typeof createObjectCollection>

/** 数字人页面的选择事实：当前配置与有效资源从集合及选择派生。 */
export function createCompanionSelection(
  collections: { configuration: ObjectCollection; avatar: ObjectCollection; voice: ObjectCollection },
) {
  let selectedConfigId = ''
  let temporaryVoiceId = ''
  // useSyncExternalStore 要求快照引用稳定：仅在选择变化时更新缓存对象。
  let snapshot: DigitalHumanSelectionView = { selectedConfigId, temporaryVoiceId }
  const observers = new Set<() => void>()
  const changed = () => {
    snapshot = { selectedConfigId, temporaryVoiceId }
    observers.forEach(listener => listener())
  }

  return {
    get selection(): DigitalHumanSelectionView {
      return snapshot
    },
    subscribe(listener: () => void) {
      observers.add(listener)
      return () => { observers.delete(listener) }
    },
    selectConfiguration(id: string | number) {
      // 重新选择配置清除临时音色；默认绑定仍需显式保存。
      selectedConfigId = String(id)
      temporaryVoiceId = ''
      changed()
    },
    selectTemporaryVoice(id: string | number) {
      temporaryVoiceId = String(id)
      changed()
    },
    resolve() {
      return resolveCompanion({
        configs: collections.configuration.getSnapshot().rows,
        avatars: collections.avatar.getSnapshot().rows,
        voices: collections.voice.getSnapshot().rows,
        selectedConfigId: selectedConfigId || String(collections.configuration.getSnapshot().rows[0]?.id ?? ''),
        temporaryVoiceId,
      })
    },
  }
}

export type CompanionSelectionStore = ReturnType<typeof createCompanionSelection>
