/** 设备职责契约：状态事实与下行预览端口；预览消费者不依赖检测业务域。 */
export type CaptureStatus = {
  connected: boolean
  frame_available: boolean
  audio: { levels: number[]; age_ms: number | null }
}

export type PulseIntervalStatus = 'available' | 'no_video' | 'no_signal' | 'collecting' | 'low_signal_quality' | 'unavailable'

export interface DevicePreview<Media> {
  connect(onDisconnected: () => void): Promise<Media>
  disconnect(): Promise<void>
}
