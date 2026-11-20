import { useMemo } from 'react'
import DetectionPage, { PhysiologyPage, type DetectionView } from '../pages/DetectionPage'
import type { CaptureStatus } from '../domain/device'
import type { DetectionGateway, DetectionMode } from '../domain/detection'
import { useDetectionReports } from './useDetectionReports'
import type { RoundView } from '../domain/detection'
import type { ReportStore } from '../application/reports'
import { detectionHref } from './routes'

export default function DetectionScreen({ store, gateway, mode, physiology, preview }: {
  store: ReportStore
  gateway: DetectionGateway
  mode: DetectionMode
  physiology: boolean
  preview: { capture: CaptureStatus | null; stream: MediaStream | null }
}) {
  const { round, otherCapture, busy, message, actions } = useDetectionReports(store, gateway, mode)
  const view: DetectionView = useMemo(() => ({
    mode,
    capture: preview.capture,
    stream: preview.stream,
    otherCapture,
    round,
    busy,
    message,
  }), [mode, preview.capture, preview.stream, otherCapture, round, busy, message])

  if (physiology && round) {
    return <PhysiologyPage result={round.result} backHref={detectionHref(mode)} />
  }
  return <DetectionPage
    view={view} actions={{
      begin: actions.begin,
      finish: actions.finish,
      cancel: actions.cancel,
      retry: actions.retry,
    }} />
}
