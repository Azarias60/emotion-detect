import { API_BASE } from './client'
import type { DevicePreview } from '../domain/device'

type PreviewTicket = { preview_session_id: string; token: string; expires_in: number }
type RemoteAnswer = { type: 'answer'; sdp: string }
const TIMEOUT_MS = 10000

/** WebRTC recvonly：只从后端的设备采集源取得视频，没有 getUserMedia 分支。 */
export function createDevicePreview(authToken: string): DevicePreview<MediaStream> {
  let generation = 0
  let peer: RTCPeerConnection | null = null
  let ticket: PreviewTicket | null = null

  async function request<T>(route: string, options: RequestInit): Promise<T> {
    const response = await fetch(`${API_BASE}${route}`, options)
    let data: unknown
    try { data = await response.json() } catch { throw new Error('设备预览服务未返回有效响应') }
    if (!response.ok) {
      const detail = data && typeof data === 'object' && 'detail' in data
        ? (data as { detail: unknown }).detail : null
      throw new Error(typeof detail === 'string' ? detail : `设备预览请求失败（${response.status}）`)
    }
    return data as T
  }

  async function cancelSession(issued: PreviewTicket | null) {
    if (!issued) return
    try {
      await request<unknown>(`/capture/preview/session/${issued.preview_session_id}`, {
        method: 'DELETE', keepalive: true, headers: { Authorization: `Bearer ${authToken}` },
      })
    } catch { /* 资源始终在本地关闭，服务端自行超时收回 */ }
  }

  async function disconnect() {
    generation++
    const current = peer
    peer = null
    const issued = ticket
    ticket = null
    current?.close()
    await cancelSession(issued)
  }

  function waitForIce(pc: RTCPeerConnection) {
    if (pc.iceGatheringState === 'complete') return Promise.resolve()
    return new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(() => {
        pc.removeEventListener('icegatheringstatechange', changed)
        reject(new Error('视频握手等待超时'))
      }, TIMEOUT_MS)
      function changed() {
        if (pc.iceGatheringState === 'complete') {
          clearTimeout(timeout)
          pc.removeEventListener('icegatheringstatechange', changed)
          resolve()
        }
      }
      pc.addEventListener('icegatheringstatechange', changed)
    })
  }

  return {
    async connect(onDisconnected) {
      if (peer) throw new Error('设备预览正在连接或已连接')
      const currentGeneration = ++generation
      let created: RTCPeerConnection | null = null
      try {
        const issued = await request<PreviewTicket>('/capture/preview/session', { method: 'POST' })
        if (currentGeneration !== generation) {
          await cancelSession(issued)
          throw new Error('设备预览连接已取消')
        }
        ticket = issued
        const pc = new RTCPeerConnection({ iceServers: [] })
        created = pc
        peer = pc
        const stream = new MediaStream()

        // 不能只以 SDP 握手成功判断视频在线；必须收到可播放的视频轨。
        // track 事件可先于 Promise 等待发生；无超时器长期悬挂。
        let resolveFrame!: (value: MediaStream) => void
        const firstFrame = new Promise<MediaStream>(resolve => { resolveFrame = resolve })
        let receivedFrame = false
        const ready = () => {
          if (receivedFrame) return
          receivedFrame = true
          resolveFrame(stream)
        }
        const lost = () => {
          if (currentGeneration !== generation) return
          onDisconnected()
        }
        pc.addEventListener('track', event => {
          if (event.track.kind !== 'video') return
          stream.addTrack(event.track)
          event.track.addEventListener('ended', lost, { once: true })
          if (!event.track.muted) ready()
          else event.track.addEventListener('unmute', ready, { once: true })
        })
        pc.addEventListener('connectionstatechange', () => {
          if (currentGeneration !== generation) return
          if (['failed', 'closed'].includes(pc.connectionState)) lost()
        })

        pc.addTransceiver('video', { direction: 'recvonly' })
        await pc.setLocalDescription(await pc.createOffer())
        await waitForIce(pc)
        if (currentGeneration !== generation) throw new Error('设备预览连接已取消')
        const answer = await request<RemoteAnswer>('/capture/preview/offer', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${issued.token}`,
          },
          body: JSON.stringify({
            type: 'offer', sdp: pc.localDescription?.sdp, preview_session_id: issued.preview_session_id,
          }),
        })
        if (currentGeneration !== generation) throw new Error('设备预览连接已取消')
        await pc.setRemoteDescription(answer)
        const activeStream = await Promise.race([
          firstFrame,
          new Promise<never>((_, reject) => {
            const timeout = setTimeout(() => reject(new Error('设备视频暂无可用帧')), TIMEOUT_MS)
            void firstFrame.then(() => clearTimeout(timeout))
          }),
        ])
        if (currentGeneration !== generation) throw new Error('设备预览连接已取消')
        return activeStream
      } catch (error) {
        if (currentGeneration === generation) await disconnect()
        else created?.close()
        throw error
      }
    },
    disconnect,
  }
}
