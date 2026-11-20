import { useSyncExternalStore } from 'react'
import { emptyObjectSnapshot, type CompanionSelectionStore, type ObjectCollection, type ObjectSnapshot } from '../application/digitalHuman'
import type { DigitalHumanSelectionView } from '../domain/digitalHuman'

/** 一类对象集合的观察快照；React 绑定归 app 层。 */
export function useObjectCollection(collection: ObjectCollection | null | undefined): ObjectSnapshot {
  return useSyncExternalStore(
    collection?.subscribe ?? (() => () => {}),
    collection?.getSnapshot ?? (() => emptyObjectSnapshot),
  )
}

export function useCompanionSelection(store: CompanionSelectionStore): DigitalHumanSelectionView {
  return useSyncExternalStore(
    store.subscribe,
    () => store.selection,
  )
}
