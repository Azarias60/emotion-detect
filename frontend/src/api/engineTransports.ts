/**
 * 数字人引擎的平台实现层（api）：所有浏览器 API 收敛在此。
 *
 * 端口契约定义在 domain/（digitalHuman.ts = live 模式，guideHuman.ts = 导览 default 模式）；
 * 本文件只负责"怎么连"：WebRTC 画面/语音、WebSocket 事件流、
 * 麦克风 AudioWorklet 采集、导览语音 PCM 播放、引擎素材管理接口。
 * 引擎默认跑在本机 8020，经 Vite 代理访问（/dh-api、/dh-ws），
 * 与 /emotion-api → server(8081) 同一模式。
 */
import type {
  DigitalHumanTransport,
  EngineAssets,
  EngineEvent,
  SegmentInfo,
  TransportDiag,
} from '../domain/companionLive'
import type { GuideEvent, GuideTransport } from '../domain/guideSession'

const HTTP_BASE = '/dh-api'
const WS_PATH = '/dh-ws/ws/conversation'

/** 麦克风重采样 Worklet：任意采样率输入 → 16kHz Int16，512 样本一包。 */
const MIC_WORKLET_SOURCE = `
class LiveTalkingMic extends AudioWorkletProcessor {
  constructor() {
    super();
    this.phase = 0;
    this.sum = 0;
    this.count = 0;
    this.output = new Int16Array(512);
    this.outputIndex = 0;
  }
  process(inputs) {
    const input = inputs[0];
    if (input && input[0] && input[0].length) {
      const source = input[0];
      for (let i = 0; i < source.length; i++) {
        this.sum += source[i];
        this.count += 1;
        this.phase += 16000;
        if (this.phase < sampleRate) continue;
        this.phase -= sampleRate;
        const value = Math.max(-1, Math.min(1, this.sum / this.count));
        this.output[this.outputIndex++] = value < 0 ? value * 32768 : value * 32767;
        this.sum = 0;
        this.count = 0;
        if (this.outputIndex === this.output.length) {
          const pcm = this.output;
          this.port.postMessage(pcm.buffer, [pcm.buffer]);
          this.output = new Int16Array(512);
          this.outputIndex = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor("livetalking-mic", LiveTalkingMic);
`

export function createDigitalHumanTransport(): DigitalHumanTransport {
  let pc: RTCPeerConnection | null = null
  let ws: WebSocket | null = null
  // open 前排队（configure 在 openEvents 刚返回、socket 仍 CONNECTING 时发出）
  const pending: string[] = []
  // 回调经可变引用转发：重复 openEvents（会话重建）时更新处理器而不是丢弃旧监听
  let eventHandler: ((event: EngineEvent) => void) | null = null
  let closeHandler: (() => void) | null = null
  let micStream: MediaStream | null = null
  let audioCtx: AudioContext | null = null
  let micNode: AudioWorkletNode | null = null
  let micAnalyser: AnalyserNode | null = null
  let micLevelBuf: Float32Array<ArrayBuffer> | null = null
  let workletReady = false

  // ── 链路诊断（仅展示用）───────────────────────────────────────
  // 音量条只证明"麦克风有输入"，上行字节只证明 PCM 真的发出去过，
  // 引擎回传计数只证明引擎有反应——三段各自独立，一眼看出链路断在哪。
  let uplinkBytes = 0
  let engineEvents = 0
  let micLevel = 0
  let audioBlocked = false
  const diagHandlers = new Set<(diag: TransportDiag) => void>()
  const emitDiag = () => {
    const snapshot: TransportDiag = { micLevel, uplinkBytes, engineEvents, audioBlocked }
    diagHandlers.forEach((handler) => handler(snapshot))
  }
  // 音量电平：150ms 读一次时域 RMS（×12 提升观感灵敏度，与原版同算法）
  let levelTimer: number | null = null
  const startLevelMeter = () => {
    if (levelTimer !== null) return
    levelTimer = window.setInterval(() => {
      if (!micAnalyser || !micLevelBuf) {
        micLevel = 0
        return
      }
      try {
        micAnalyser.getFloatTimeDomainData(micLevelBuf)
      } catch {
        return
      }
      let sum = 0
      for (let i = 0; i < micLevelBuf.length; i += 1) sum += micLevelBuf[i] * micLevelBuf[i]
      micLevel = Math.min(1, Math.sqrt(sum / micLevelBuf.length) * 12)
      emitDiag()
    }, 150)
  }
  const stopLevelMeter = () => {
    if (levelTimer !== null) {
      clearInterval(levelTimer)
      levelTimer = null
    }
    micLevel = 0
  }
  // AudioContext 挂起自愈：切后台回来浏览器可能挂起上下文（麦开着但采样停了），
  // 低频看门狗发现 suspended 就尝试 resume，并置提示位
  let keepAliveTimer: number | null = null
  const startCtxKeepAlive = () => {
    if (keepAliveTimer !== null) return
    keepAliveTimer = window.setInterval(() => {
      if (!audioCtx || audioCtx.state === 'closed') {
        audioBlocked = false
        emitDiag()
        return
      }
      if (audioCtx.state === 'suspended' && micStream) {
        void audioCtx.resume().catch(() => undefined)
        if (!audioBlocked) {
          audioBlocked = true
          emitDiag()
        }
      } else if (audioBlocked) {
        audioBlocked = false
        emitDiag()
      }
    }, 1000)
  }

  const json = async <T>(path: string, init?: RequestInit): Promise<T> => {
    const response = await fetch(`${HTTP_BASE}${path}`, init)
    if (!response.ok) throw new Error(`引擎接口 ${path} 返回 ${response.status}`)
    return response.json() as Promise<T>
  }

  /** 与 json() 的区别：失败时读出响应体里的 error/message（生成/删除接口靠它提示"重名"等原因）。 */
  const jsonDetail = async <T>(path: string, init?: RequestInit): Promise<T> => {
    const response = await fetch(`${HTTP_BASE}${path}`, init)
    const body = await response.json().catch(() => null) as (Record<string, unknown> | null)
    if (!response.ok) {
      const detail = body && (body.error || body.message || body.msg)
      throw new Error(typeof detail === 'string' && detail ? detail : `引擎接口 ${path} 返回 ${response.status}`)
    }
    return (body ?? {}) as T
  }

  const post = (path: string, payload: Record<string, unknown>): RequestInit => ({
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })

  return {
    async ready() {
      const data = await json<{ ready?: boolean }>('/api/readiness').catch(() => null)
      return Boolean(data?.ready)
    },

    async openRtc(video, avatar) {
      pc = new RTCPeerConnection({ iceServers: [] })
      pc.addTransceiver('video', { direction: 'recvonly' })
      pc.addTransceiver('audio', { direction: 'recvonly' })
      const offer = await pc.createOffer()
      await pc.setLocalDescription(offer)
      // avatar 随 offer 下发：换形象重连时服务端按这个 id 建会话
      const body: Record<string, unknown> = {
        sdp: pc.localDescription?.sdp,
        type: pc.localDescription?.type,
      }
      if (avatar) body.avatar = avatar
      const answer = await jsonDetail<{ sdp: string; type: RTCSdpType; sessionid?: string }>('/offer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      await pc.setRemoteDescription({ sdp: answer.sdp, type: answer.type })
      video.srcObject = new MediaStream(pc.getReceivers().map((receiver) => receiver.track))
      await video.play().catch(() => undefined)
      return String(answer.sessionid ?? '')
    },

    openEvents(_sessionId, onEvent, onClose) {
      eventHandler = onEvent
      closeHandler = onClose
      if (ws && ws.readyState <= 1) return
      const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
      const socket = new WebSocket(`${protocol}://${location.host}${WS_PATH}`)
      socket.binaryType = 'arraybuffer'
      socket.addEventListener('open', () => {
        // open 前排队的 configure 等消息在这里补发
        while (pending.length) socket.send(pending.shift()!)
      })
      socket.addEventListener('message', (message) => {
        if (typeof message.data !== 'string') return
        engineEvents += 1
        try {
          eventHandler?.(JSON.parse(message.data) as EngineEvent)
        } catch {
          /* 引擎偶发非 JSON 帧：忽略，不影响会话 */
        }
      })
      socket.addEventListener('close', () => {
        // 已被 closeMedia/重连替换的旧 socket：静默退场，不触发上层断线处理
        if (ws !== socket) return
        ws = null
        closeHandler?.()
      })
      ws = socket
    },

    send(payload) {
      const text = JSON.stringify(payload)
      if (ws && ws.readyState === 1) ws.send(text)
      else pending.push(text)
    },

    sendPcm(buffer) {
      if (ws && ws.readyState === 1) {
        ws.send(buffer)
        uplinkBytes += buffer.byteLength || 0
      }
    },

    async startMic(sendPcm) {
      if (micStream) return
      audioCtx = new AudioContext()
      // 授权提示可能永不返回：部分内嵌浏览器/已静默拒绝的环境里
      // getUserMedia 既不 resolve 也不 reject（实测挂死），用户点了按钮
      // 会毫无反馈。加超时把它变成可解释的失败，交给上层给引导文案。
      micStream = await new Promise<MediaStream>((resolve, reject) => {
        const timer = setTimeout(() => {
          const error = new Error('麦克风授权没有响应')
          error.name = 'TimeoutError'
          reject(error)
        }, 8000)
        navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
        }).then(
          (stream) => { clearTimeout(timer); resolve(stream) },
          (error) => { clearTimeout(timer); reject(error) },
        )
      })
      if (!workletReady) {
        const url = URL.createObjectURL(new Blob([MIC_WORKLET_SOURCE], { type: 'application/javascript' }))
        try {
          await audioCtx.audioWorklet.addModule(url)
          workletReady = true
        } finally {
          URL.revokeObjectURL(url)
        }
      }
      const source = audioCtx.createMediaStreamSource(micStream)
      const node = new AudioWorkletNode(audioCtx, 'livetalking-mic')
      node.port.onmessage = (message) => sendPcm(message.data as ArrayBuffer)
      source.connect(node)
      // 音量电平分析器：只做诊断/UI 显示，不参与上行链路
      try {
        micAnalyser = audioCtx.createAnalyser()
        micAnalyser.fftSize = 512
        micLevelBuf = new Float32Array(micAnalyser.fftSize)
        source.connect(micAnalyser)
      } catch {
        micAnalyser = null
        micLevelBuf = null
      }
      // 零增益下沉：拉活 Worklet 又不外放，避免回声
      const sink = audioCtx.createGain()
      sink.gain.value = 0
      node.connect(sink)
      sink.connect(audioCtx.destination)
      micNode = node
      startLevelMeter()
      startCtxKeepAlive()
      uplinkBytes = 0
      engineEvents = 0
      emitDiag()
    },

    stopMic() {
      stopLevelMeter()
      micNode?.disconnect()
      micNode = null
      micAnalyser = null
      micLevelBuf = null
      micStream?.getTracks().forEach((track) => track.stop())
      micStream = null
      void audioCtx?.close().catch(() => undefined)
      audioCtx = null
      workletReady = false
      audioBlocked = false
      emitDiag()
    },

    resume() {
      if (audioCtx && audioCtx.state === 'suspended') void audioCtx.resume().catch(() => undefined)
    },

    async isSpeaking(sessionId) {
      const data = await json<{ data?: boolean }>('/is_speaking', post('/is_speaking', { sessionid: sessionId })).catch(() => null)
      return Boolean(data?.data)
    },

    closeMedia() {
      // 只断画面/事件通道（换形象重连用）：麦克风轨道保持活着，
      // 重连成功后 PCM 继续从同一条 Worklet 链路走。
      // 先把 ws 置空再 close：close 监听里 ws !== socket 就不会上报断线
      const socket = ws
      ws = null
      socket?.close()
      pending.length = 0
      pc?.close()
      pc = null
    },

    async assets() {
      return json<EngineAssets>('/api/assets').catch(() => null)
    },

    async setVoice(id) {
      const data = await jsonDetail<{ activeVoice?: string }>('/api/voice', post('/api/voice', { id }))
      return data.activeVoice ?? null
    },

    async generateAvatar(name) {
      const data = await jsonDetail<{ message?: string }>('/api/avatar/generate', post('/api/avatar/generate', { name }))
      return data.message ?? ''
    },

    async deleteAvatar(id) {
      const data = await jsonDetail<{ message?: string }>('/api/avatar/delete', post('/api/avatar/delete', { id }))
      return data.message ?? ''
    },

    async segment(sessionId) {
      const data = await json<{ code?: number; data?: { segment?: string; kind?: string } }>(
        `/api/session/segment?sessionid=${encodeURIComponent(sessionId)}`,
      ).catch(() => null)
      if (!data || data.code !== 0 || !data.data?.segment) return null
      return { segment: data.data.segment, kind: data.data.kind ?? '' }
    },

    async dance(sessionId) {
      const data = await jsonDetail<{ code?: number; msg?: string }>('/dance', post('/dance', { sessionid: sessionId }))
      if (data.code !== undefined && data.code !== 0) return { ok: false, message: data.msg || '当前形象没有舞蹈段' }
      return { ok: true }
    },

    interruptTalk(sessionId) {
      // 会话失效时静默：本地状态照常复位
      void json('/interrupt_talk', post('/interrupt_talk', { sessionid: sessionId })).catch(() => undefined)
    },

    humanText(sessionId, text) {
      void json('/human', post('/human', { type: 'chat', text, sessionid: sessionId, interrupt: true }))
        .catch(() => undefined)
    },

    async personas() {
      return json<{ personas: { id: string; name: string }[]; active: string }>('/api/personas?mode=live').catch(() => null)
    },

    async setPersona(id) {
      await jsonDetail('/api/persona', post('/api/persona', { id }))
    },

    onDiagnostics(handler) {
      diagHandlers.add(handler)
      handler({ micLevel, uplinkBytes, engineEvents, audioBlocked })
      return () => {
        if (diagHandlers.has(handler)) diagHandlers.delete(handler)
      }
    },

    closeAll() {
      this.stopMic()
      const socket = ws
      ws = null
      socket?.close()
      pending.length = 0
      pc?.close()
      pc = null
    },
  }
}

// ═══ 导览页（default 模式）：事件流 + 暖暖语音播放 ═══════════════════════

/** 暖暖语音播放器：16kHz 单声道 PCM 块按序无缝调度（api 层持有 AudioContext）。 */
class PcmSequencer {
  private context: AudioContext | null = null
  private nextAt = 0
  private pending = 0
  private offTimer: number | null = null
  constructor(private readonly onEdge: (active: boolean) => void) {}

  play(pcm: ArrayBuffer) {
    if (!this.context) this.context = new AudioContext({ sampleRate: 16000 })
    const context = this.context
    void context.resume().catch(() => undefined)
    const raw = new Int16Array(pcm)
    if (raw.length === 0) return
    // 段落间的短空隙不算"说完"：清掉待发的收尾通知，避免状态条闪烁
    if (this.offTimer !== null) {
      clearTimeout(this.offTimer)
      this.offTimer = null
    }
    const audio = context.createBuffer(1, raw.length, 16000)
    const channel = audio.getChannelData(0)
    for (let i = 0; i < raw.length; i += 1) channel[i] = raw[i] / 32768
    const source = context.createBufferSource()
    source.buffer = audio
    source.connect(context.destination)
    // 首块从"现在"起播，后续块无缝衔接上一块的结尾
    const startAt = Math.max(context.currentTime + 0.02, this.nextAt)
    source.start(startAt)
    this.nextAt = startAt + audio.duration
    this.pending += 1
    if (this.pending === 1) this.onEdge(true)
    source.onended = () => {
      this.pending -= 1
      if (this.pending <= 0) {
        this.pending = 0
        this.nextAt = 0
        // 200ms 收尾宽限：引擎分段回传，段边界不能判成播放结束
        this.offTimer = window.setTimeout(() => {
          this.offTimer = null
          this.onEdge(false)
        }, 200)
      }
    }
  }

  stop() {
    if (this.offTimer !== null) {
      clearTimeout(this.offTimer)
      this.offTimer = null
    }
    this.pending = 0
    this.nextAt = 0
    void this.context?.close().catch(() => undefined)
    this.context = null
    this.onEdge(false)
  }
}

export function createGuideTransport(): GuideTransport {
  const sequencer = new PcmSequencer((active) => playbackEdge?.(active))
  let playbackEdge: (active: boolean) => void = () => undefined
  let ws: WebSocket | null = null
  let micStream: MediaStream | null = null
  let audioCtx: AudioContext | null = null
  let micNode: AudioWorkletNode | null = null
  let workletReady = false
  // 离开首页后为 true：WS 继续收事件，但下行 PCM 直接丢弃（不在别的页面外放）
  let playbackSuppressed = false
  // open 前发出的消息排队：configure 在 openEvents 后立即调用，
  // 此时 socket 还在 CONNECTING，直接 send 会被静默丢弃
  const pending: string[] = []
  // 回调经可变引用转发：重复 openEvents 时更新处理器，旧 socket 的监听继续有效
  let eventHandler: ((event: GuideEvent) => void) | null = null
  let closeHandler: (() => void) | null = null

  return {
    onPlayback(edge) {
      playbackEdge = edge
      return () => {
        if (playbackEdge === edge) playbackEdge = () => undefined
      }
    },

    async ready() {
      try {
        const response = await fetch(`${HTTP_BASE}/api/readiness`)
        if (!response.ok) return false
        const data = await response.json().catch(() => null)
        return Boolean(data?.ready)
      } catch {
        return false
      }
    },

    openEvents(onEvent, onClose) {
      eventHandler = onEvent
      closeHandler = onClose
      if (ws && ws.readyState <= 1) return
      const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
      const socket = new WebSocket(`${protocol}://${location.host}${WS_PATH}`)
      socket.binaryType = 'arraybuffer'
      socket.addEventListener('open', () => {
        // open 前排队的 configure 等消息在这里补发
        while (pending.length) socket.send(pending.shift()!)
      })
      socket.addEventListener('message', (message) => {
        if (typeof message.data === 'string') {
          try {
            eventHandler?.(JSON.parse(message.data) as GuideEvent)
          } catch {
            /* 引擎偶发非 JSON 帧：忽略 */
          }
          return
        }
        // 二进制帧 = 暖暖语音：首字节是 0x01 协议前缀，其余是 16kHz Int16 PCM。
        // 由本层播放器排程外放（播放边沿经 onPlayback 上报给应用层）。
        // SPA 已离开首页时丢弃：WS 为会话保活而保留，但声音不能追到别的页面。
        if (playbackSuppressed) return
        if (message.data instanceof ArrayBuffer && message.data.byteLength > 1) {
          sequencer.play(message.data.slice(1))
        }
      })
      socket.addEventListener('close', () => {
        if (ws === socket) ws = null
        closeHandler?.()
      })
      ws = socket
    },

    send(payload) {
      const text = JSON.stringify(payload)
      if (ws && ws.readyState === 1) ws.send(text)
      else pending.push(text)
    },

    sendPcm(buffer) {
      if (ws && ws.readyState === 1) ws.send(buffer)
    },

    async startMic(sendPcm) {
      if (micStream) return
      audioCtx = new AudioContext()
      micStream = await new Promise<MediaStream>((resolve, reject) => {
        const timer = setTimeout(() => {
          const error = new Error('麦克风授权没有响应')
          error.name = 'TimeoutError'
          reject(error)
        }, 8000)
        navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
        }).then(
          (stream) => { clearTimeout(timer); resolve(stream) },
          (error) => { clearTimeout(timer); reject(error) },
        )
      })
      if (!workletReady) {
        const url = URL.createObjectURL(new Blob([MIC_WORKLET_SOURCE], { type: 'application/javascript' }))
        try {
          await audioCtx.audioWorklet.addModule(url)
          workletReady = true
        } finally {
          URL.revokeObjectURL(url)
        }
      }
      const source = audioCtx.createMediaStreamSource(micStream)
      const node = new AudioWorkletNode(audioCtx, 'livetalking-mic')
      node.port.onmessage = (message) => sendPcm(message.data as ArrayBuffer)
      source.connect(node)
      const sink = audioCtx.createGain()
      sink.gain.value = 0
      node.connect(sink)
      sink.connect(audioCtx.destination)
      micNode = node
    },

    stopMic() {
      micNode?.disconnect()
      micNode = null
      micStream?.getTracks().forEach((track) => track.stop())
      micStream = null
      void audioCtx?.close().catch(() => undefined)
      audioCtx = null
      workletReady = false
    },

    resume() {
      if (audioCtx && audioCtx.state === 'suspended') void audioCtx.resume().catch(() => undefined)
    },

    stopPlayback() {
      sequencer.stop()
    },

    suppressPlayback(on: boolean) {
      playbackSuppressed = on
      if (on) sequencer.stop()
    },

    closeAll() {
      playbackSuppressed = false
      this.stopMic()
      sequencer.stop()
      if (ws) {
        ws.onclose = null
        ws.close()
        ws = null
      }
    },
  }
}
