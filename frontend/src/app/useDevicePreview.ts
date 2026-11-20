import { useEffect, useState } from 'react'
import { createPreviewSupervisor, type PreviewUpdate } from '../application/preview'
import type { CaptureStatus, DevicePreview } from '../domain/device'

/** 页面使用期内观察设备并保持下行预览；离开页面即释放连接。 */
export function useDevicePreview(readStatus: () => Promise<CaptureStatus>, device: DevicePreview<MediaStream> | null, requested: boolean) {
  const [capture, setCapture] = useState<PreviewUpdate<MediaStream>>({})
  useEffect(() => {
    setCapture({})
    if (!device || !requested) return
    const monitor = createPreviewSupervisor(readStatus, device, update =>
      setCapture(previous => ({ ...previous, ...update })))
    void monitor.sync()
    const timer = window.setInterval(() => void monitor.sync(), 6000)
    return () => {
      window.clearInterval(timer)
      void monitor.dispose()
    }
  }, [device, requested, readStatus])
  return { capture }
}
