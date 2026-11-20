import type { CaptureStatus, DevicePreview } from '../domain/device'

export type PreviewUpdate<Media> = {
  capture?: CaptureStatus | null
  stream?: Media | null
}

/** 同一设备采集源：页面进入即同步状态并建立下行预览；离线时始终返回占位态。
 * 无 React、DOM、WebRTC、HTTP 或定时器依赖，周期调度由外层决定。
 */
export function createPreviewSupervisor<Media>(
  readStatus: () => Promise<CaptureStatus>,
  preview: DevicePreview<Media>,
  publish: (update: PreviewUpdate<Media>) => void,
) {
  let disposed = false
  let busy = false
  let live = false

  async function reset() {
    live = false
    publish({ stream: null })
    await preview.disconnect()
  }

  return {
    async sync() {
      if (disposed || busy) return
      busy = true
      try {
        const capture = await readStatus().catch(() => null)
        if (disposed) return
        if (!capture) return
        publish({ capture })
        if (!capture.connected || !capture.frame_available) {
          if (live && !capture.connected) await reset()
          return
        }
        if (live) return
        const stream = await preview.connect(() => {
          if (disposed) return
          void reset().catch(() => {})
        })
        if (disposed) return
        live = true
        publish({ stream })
      } catch {
        if (!disposed) await reset()
      } finally {
        busy = false
      }
    },
    async dispose() {
      if (disposed) return
      disposed = true
      live = false
      await preview.disconnect()
    },
  }
}
