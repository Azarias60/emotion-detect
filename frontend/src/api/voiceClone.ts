/**
 * 语音克隆的平台实现（api）：麦克风 AudioWorklet 采集 → 真 WAV 编码 →
 * 上传后端质检（8081）。不用 MediaRecorder：它产出 webm/opus 容器，
 * 后端质检只收 RIFF WAV（2026-11-13 原版教训）。
 */
import type { OfficialVoice, ClonePort } from '../domain/voiceClone'

const API_BASE = '/emotion-api'
const TARGET_SR = 16000

/** 录音采集 Worklet：麦克风 → 16kHz 单声道 Float32 分片推给主线程。 */
const RECORDER_WORKLET = `
class VoiceCloneRecorder extends AudioWorkletProcessor {
  constructor() {
    super()
    this.pcm = []
  }
  process(inputs) {
    const input = inputs[0]
    if (input && input[0] && input[0].length) {
      this.pcm.push(new Float32Array(input[0]))
      this.port.postMessage(this.pcm[this.pcm.length - 1])
    }
    return true
  }
}
registerProcessor("voice-clone-recorder", VoiceCloneRecorder)
`

/** Float32 分片 → 16kHz 单声道 16bit WAV Blob（44 字节头 + PCM）。 */
function encodeWav(chunks: Float32Array[], sampleRate: number): Blob {
  const total = chunks.reduce((sum, part) => sum + part.length, 0)
  const buffer = new ArrayBuffer(44 + total * 2)
  const view = new DataView(buffer)
  const ascii = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i))
  }
  ascii(0, 'RIFF')
  view.setUint32(4, 36 + total * 2, true)
  ascii(8, 'WAVE')
  ascii(12, 'fmt ')
  view.setUint32(16, 16, true) // PCM 头块长度
  view.setUint16(20, 1, true) // 格式：PCM
  view.setUint16(22, 1, true) // 单声道
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * 2, true) // 字节率
  view.setUint16(32, 2, true) // 块对齐
  view.setUint16(34, 16, true) // 位深
  ascii(36, 'data')
  view.setUint32(40, total * 2, true)
  let offset = 44
  for (const part of chunks) {
    for (let i = 0; i < part.length; i += 1) {
      // NaN/Inf 显式归零：Math clamp 对 NaN 无效（NaN<0 为 false 会走进 NaN*0x7fff）
      const raw = part[i]
      const sample = Number.isFinite(raw) ? Math.max(-1, Math.min(1, raw)) : 0
      view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true)
      offset += 2
    }
  }
  return new Blob([buffer], { type: 'audio/wav' })
}

/** 上下文采样率不是 16k 时做线性插值（浏览器普遍支持 16k，这条是兜底）。 */
function resampleTo16k(chunks: Float32Array[], fromRate: number): Float32Array[] {
  if (!chunks.length) return chunks
  if (!(fromRate > 0)) return chunks // 脏采样率不猜，原样返回让质检去拒
  if (fromRate === TARGET_SR) return chunks
  const merged = new Float32Array(chunks.reduce((sum, part) => sum + part.length, 0))
  let cursor = 0
  for (const part of chunks) {
    merged.set(part, cursor)
    cursor += part.length
  }
  const target = Math.round((merged.length * TARGET_SR) / fromRate)
  const out = new Float32Array(target)
  for (let i = 0; i < target; i += 1) {
    const pos = (i * fromRate) / TARGET_SR
    const left = Math.floor(pos)
    const right = Math.min(left + 1, merged.length - 1)
    out[i] = merged[left] + (merged[right] - merged[left]) * (pos - left)
  }
  return [out]
}

interface RecordingSession {
  stream: MediaStream
  context: AudioContext
  source: MediaStreamAudioSourceNode
  node: AudioWorkletNode
  sink: GainNode
  chunks: Float32Array[]
  workletReady: boolean
}

export function createVoiceClonePort(): ClonePort {
  let session: RecordingSession | null = null

  const releaseMic = () => {
    const current = session
    session = null
    if (!current) return
    try {
      current.node.disconnect()
      current.source.disconnect()
      current.sink.disconnect()
      current.stream.getTracks().forEach((track) => track.stop())
    } catch {
      /* 已断开时忽略 */
    }
    if (current.context.state !== 'closed') void current.context.close().catch(() => undefined)
  }

  const getJson = async <T>(path: string): Promise<T> => {
    const response = await fetch(`${API_BASE}${path}`)
    if (!response.ok) throw new Error(`接口 ${path} 返回 ${response.status}`)
    return response.json() as Promise<T>
  }

  return {
    async sentences() {
      try {
        const body = await getJson<{ data?: { sentences?: string[] } }>('/dh/voice/sentences')
        return body.data?.sentences ?? []
      } catch {
        return []
      }
    },

    async officialVoices() {
      try {
        const body = await getJson<{ data?: { voices?: OfficialVoice[] } }>('/dh/voice/official')
        return body.data?.voices ?? []
      } catch {
        return []
      }
    },

    async startRecording() {
      if (session) return
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
      })
      const context = new AudioContext({ sampleRate: TARGET_SR })
      const workletUrl = URL.createObjectURL(new Blob([RECORDER_WORKLET], { type: 'text/javascript' }))
      try {
        await context.audioWorklet.addModule(workletUrl)
      } finally {
        URL.revokeObjectURL(workletUrl)
      }
      const node = new AudioWorkletNode(context, 'voice-clone-recorder')
      const source = context.createMediaStreamSource(stream)
      // 零增益下沉：拉活 Worklet 又不外放，避免回声
      const sink = context.createGain()
      sink.gain.value = 0
      source.connect(node)
      node.connect(sink)
      sink.connect(context.destination)
      session = { stream, context, source, node, sink, chunks: [], workletReady: true }
      node.port.onmessage = (message) => {
        if (session) session.chunks.push(new Float32Array(message.data as ArrayBuffer))
      }
    },

    async stopRecording() {
      const current = session
      if (!current) return null
      const rate = current.context.sampleRate
      const chunks = current.chunks
      current.chunks = []
      releaseMic()
      const sampleCount = chunks.reduce((sum, part) => sum + part.length, 0)
      // 空录音在编码前按样本数判断：只有 44 字节头的 WAV size 也非 0，
      // 走后端质检会拿到一句莫名其妙的格式错误
      if (!sampleCount) return null
      const wav = encodeWav(resampleTo16k(chunks, rate), TARGET_SR)
      if (!wav.size) return null
      const seconds = Math.round((sampleCount / rate) * 10) / 10
      return { wav, seconds }
    },

    async upload(wav) {
      const form = new FormData()
      form.append('audio', wav, 'voice.wav')
      const response = await fetch(`${API_BASE}/dh/voice/upload`, { method: 'POST', body: form })
      const body = await response.json().catch(() => null) as ({
        detail?: string
        data?: { voice_id?: string; info?: { duration?: number } }
      } | null)
      if (!response.ok) throw new Error(body?.detail || `上传失败 HTTP ${response.status}`)
      const voiceId = body?.data?.voice_id ?? ''
      if (!voiceId) throw new Error('上传成功但没有返回音色 id')
      return { voiceId, duration: body?.data?.info?.duration }
    },

    async bind(voiceId, avatarId) {
      const response = await fetch(`${API_BASE}/dh/voice/bind`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ voice_id: voiceId, avatar_id: avatarId }),
      })
      const body = await response.json().catch(() => null) as { detail?: string } | null
      if (!response.ok) throw new Error(body?.detail || `绑定失败 HTTP ${response.status}`)
    },
  }
}
