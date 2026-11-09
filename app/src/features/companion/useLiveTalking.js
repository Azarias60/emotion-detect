import { onBeforeUnmount, ref } from 'vue'

/**
 * LiveTalking 实时数字人客户端（引擎本体在 digital-human/livetalking/，默认服务端口 8020）。
 * 两条通道：
 *   ① WebRTC（POST /offer）：wav2lip 实时口型画面 + CosyVoice3 语音，媒体流直接进 <video>
 *   ② WebSocket（/ws/conversation）：麦克风 PCM / 文字 → 服务端 ASR → LLM → TTS，
 *      事件流回传（transcript / assistant_delta / audio_start / response_done …）
 * 协议与 LiveTalking web/app.js 保持一致（configure/text/cancel + 16kHz Int16 512 帧分包）。
 */

const BASE = (import.meta.env.VITE_LIVETALKING_URL || 'http://127.0.0.1:8020').replace(/\/+$/, '')

// 麦克风重采样 Worklet（与 LiveTalking 同款）：把任意采样率的输入转成 16kHz Int16，512 帧打一包
const WORKLET_SOURCE = `
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

export function useLiveTalking() {
  // ---------- 对外状态 ----------
  const connection = ref('idle') // idle | connecting | connected | failed
  const connectionTitle = ref('未连接')
  const connectionDetail = ref('正在检测数字人服务…')
  const sessionId = ref('')
  const serviceReady = ref(false)
  const listening = ref(false)
  const busy = ref(false)
  const speaking = ref(false)
  // 对话情绪（引擎侧逐句分类推出）：emotion/label/consecutive/hint
  const emotion = ref(null)
  // 连续同情绪触发的模块推荐（页面弹资料卡用）
  const recommendation = ref(null)
  // 资料框内容：只展示 LLM 回答带出的真实要点（doc 事件）。
  // 曾预置"和小C聊天"教学卡（2026-10-26 用户拍板删除：用户不需要被教
  // 怎么用，看就行）
  const docCard = ref(null)
  const pipeline = ref(0) // 0 空闲 · 1 语音识别 · 2 大模型 · 3 语音合成
  const messages = ref([]) // { role: user | assistant | action, content }
  const personas = ref([])
  const activePersona = ref('')
  const assets = ref(null)
  const currentAvatar = ref('') // 当前选择的形象 id（/offer 时带给服务端）
  const dancing = ref(false) // 舞蹈预加载中
  const needPlayTap = ref(false)

  // ---------- 内部句柄 ----------
  let pc = null
  let ws = null
  let mediaStream = null
  let videoEl = null
  let audioCtx = null
  let micSource = null
  let micNode = null
  let micSink = null
  let micStream = null
  let workletReady = false
  let userBubble = -1
  let assistantBubble = -1
  let disposed = false
  let readinessTimer = null
  let speakingWatchTimer = null
  let connectDetail = '' // 连接中的自定义说明（切换数字人时用）
  let assetPollTimer = null // 素材生成轮询
  let personaId = '' // 连接级人设（导览页传 guide：引擎侧人设+情绪推荐角色都会按它分工）

  onBeforeUnmount(teardown)

  function teardown() {
    disposed = true
    stopReadinessPolling()
    stopSpeakingWatch()
    stopAssetPolling()
    try {
      ws?.close()
    } catch {}
    pc?.close()
    pc = null
    ws = null
    stopMicTracks()
    if (videoEl) videoEl.srcObject = null
  }

  function setConnection(kind, title, detail) {
    connection.value = kind
    connectionTitle.value = title
    connectionDetail.value = detail
  }

  async function api(path, options) {
    const res = await fetch(BASE + path, options)
    if (!res.ok) throw new Error(`${path} → HTTP ${res.status}`)
    return res.json()
  }

  /** 与 api() 的区别：失败时把服务端响应体里的 error/message 读出来（生成人物等接口靠它提示“重名”等原因）。 */
  async function apiDetail(path, options) {
    const res = await fetch(BASE + path, options)
    let body = null
    try {
      body = await res.json()
    } catch {}
    if (!res.ok) {
      const msg = (body && (body.error || body.message || body.msg)) || `${path} → HTTP ${res.status}`
      throw new Error(msg)
    }
    return body || {}
  }

  // ---------- 服务就绪探测（模型加载约 10~30 秒） ----------
  function stopReadinessPolling() {
    if (readinessTimer) {
      clearInterval(readinessTimer)
      readinessTimer = null
    }
  }

  async function checkReadiness() {
    // 探针文案只在还没开始连接时写：连接中/已连接时若被覆写，
    // 字幕条会把「正在建立实时通道」这类过期文案显示在已连接的画面下（三处口径打架）
    const canWrite = connection.value === 'idle'
    try {
      const data = await api('/api/readiness')
      serviceReady.value = Boolean(data.ready)
      if (data.ready) {
        if (canWrite) connectionDetail.value = '服务就绪，正在建立实时通道…'
        return true
      }
      if (canWrite) connectionDetail.value = '数字人模型加载中（首次约半分钟）…'
    } catch {
      serviceReady.value = false
      if (canWrite) connectionDetail.value = '数字人服务暂时没连上，请稍等几秒后重试；若多次失败请联系管理员'
    }
    return false
  }

  /** 页面挂载后调用：轮询就绪态，就绪即自动建立 WebRTC（只 boot 一次）。
   *  options.persona：连接级人设 id（如导览页 'guide'），随 configure 下发。 */
  function init(video, options = {}) {
    videoEl = video
    personaId = options.persona || ''
    if (disposed) return
    let booted = false
    const boot = async () => {
      if (booted) return
      booted = true
      stopReadinessPolling() // 关键：不停轮询的话，3 秒后的探针会覆写已建立连接的文案
      await loadAssets()
      if (!currentAvatar.value && assets.value?.defaultAvatar) {
        currentAvatar.value = assets.value.defaultAvatar
      }
      connect()
    }
    checkReadiness().then((ok) => {
      if (ok && !disposed) boot()
    })
    readinessTimer = setInterval(async () => {
      const ok = await checkReadiness()
      if (ok && !disposed) boot()
    }, 3000)
  }

  // ---------- WebRTC ----------
  function waitForIceGathering(peer) {
    if (peer.iceGatheringState === 'complete') return Promise.resolve()
    return new Promise((resolve) => {
      const check = () => {
        if (peer.iceGatheringState === 'complete') {
          peer.removeEventListener('icegatheringstatechange', check)
          resolve()
        }
      }
      peer.addEventListener('icegatheringstatechange', check)
    })
  }

  function waitForFirstFrame(video, timeoutMs = 20000) {
    if (video.readyState >= 2 && video.videoWidth) return Promise.resolve()
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        cleanup()
        reject(new Error('实时画面首帧等待超时'))
      }, timeoutMs)
      const ready = () => {
        if (video.readyState >= 2 && video.videoWidth) {
          cleanup()
          resolve()
        }
      }
      const cleanup = () => {
        clearTimeout(timer)
        video.removeEventListener('loadeddata', ready)
        video.removeEventListener('playing', ready)
      }
      video.addEventListener('loadeddata', ready)
      video.addEventListener('playing', ready)
    })
  }

  async function connect() {
    const video = videoEl
    if (!video || pc || connection.value === 'connecting') return
    setConnection('connecting', '正在连接数字人', connectDetail || '正在建立 WebRTC 音视频通道…')
    try {
      pc = new RTCPeerConnection({ iceServers: [] })
      mediaStream = new MediaStream()
      video.srcObject = mediaStream
      pc.addTransceiver('video', { direction: 'recvonly' })
      pc.addTransceiver('audio', { direction: 'recvonly' })
      pc.addEventListener('track', (evt) => {
        mediaStream.addTrack(evt.track)
        video.play().catch(() => {
          needPlayTap.value = true
        })
      })
      pc.addEventListener('connectionstatechange', () => {
        if (pc && pc.connectionState === 'failed') {
          setConnection('failed', '连接已中断', 'WebRTC 通道断开，请重试')
        }
      })
      await pc.setLocalDescription(await pc.createOffer())
      await waitForIceGathering(pc)
      const desc = pc.localDescription
      // avatar 带给服务端：换形象时重连，服务端按这个 id 建会话
      const offerBody = { sdp: desc.sdp, type: desc.type }
      if (currentAvatar.value) offerBody.avatar = currentAvatar.value
      const answer = await api('/offer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(offerBody),
      })
      sessionId.value = answer.sessionid
      await pc.setRemoteDescription(answer)
      await video.play().catch(() => {
        needPlayTap.value = true
      })
      await waitForFirstFrame(video)
      setConnection('connected', '实时数字人已连接', '口型与语音同步直播中')
      openSocket()
      loadPersonas()
      loadAssets()
    } catch (err) {
      try {
        pc?.close()
      } catch {}
      pc = null
      setConnection('failed', '连接失败', String(err?.message || err))
    }
  }

  function tapPlay() {
    videoEl?.play()
      .then(() => {
        needPlayTap.value = false
      })
      .catch(() => {})
  }

  // ---------- 对话 WebSocket ----------
  function openSocket() {
    if (ws && ws.readyState <= 1) return
    const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
    const url = `${protocol}://${new URL(BASE).host}/ws/conversation`
    ws = new WebSocket(url)
    ws.binaryType = 'arraybuffer'
    ws.addEventListener('open', () => {
      // live 模式 + 会话号：文字/语音都走实时会话（回复文本与口型语音同时回来）。
      // persona 可选：导览页传 guide，服务端按连接覆盖人设并切模块推荐角色
      const cfg = { type: 'configure', mode: 'live', sessionid: sessionId.value }
      if (personaId) cfg.persona = personaId
      ws.send(JSON.stringify(cfg))
    })
    ws.addEventListener('message', onSocketMessage)
    ws.addEventListener('close', () => {
      ws = null
      if (listening.value) {
        listening.value = false
        stopMicTracks()
      }
    })
    ws.addEventListener('error', () => {
      setConnection('failed', '通话通道断开', '实时对话连接中断，请重试')
    })
  }

  function onSocketMessage(message) {
    if (typeof message.data === 'string') {
      try {
        handleEvent(JSON.parse(message.data))
      } catch {}
      return
    }
    // 二进制 PCM 只在 default 模式（浏览器本地播放）使用；实时模式语音走 WebRTC，忽略
  }

  function pushMessage(role, content) {
    messages.value.push({ role, content })
    if (messages.value.length > 200) messages.value.splice(0, messages.value.length - 200)
    return messages.value.length - 1
  }

  function handleEvent(event) {
    switch (event.type) {
      case 'configured':
        break
      case 'hint':
        // 轻提示（如"没听清"）：不改连接/说话状态，只给一条灰字
        if (event.message) pushMessage('action', event.message)
        break
      case 'awake':
        connectionDetail.value = '麦克风已开启，直接说就好'
        break
      case 'wake_word_pending':
        // 连接后 connectionDetail 没有展示位，引导类文案直接进消息流，用户看得见
        pushMessage('action', `说「${event.wakeWord || '你好'}」唤醒数字人`)
        break
      case 'wake':
        pushMessage('action', '已唤醒，请直接说')
        break
      case 'emotion': {
        emotion.value = {
          key: event.emotion,
          label: event.label,
          changed: !!event.changed,
          consecutive: event.consecutive || 1,
          hint: event.hint || '',
        }
        break
      }
      case 'doc': {
        docCard.value = {
          kind: 'doc',
          title: event.title || '重要资料',
          points: event.points || [],
        }
        break
      }
      case 'recommend': {
        // 连续两句同一负向情绪：引擎按页面角色发不同推荐——
        // 导览页 kind=module（推荐检测模块，带 module key）；
        // 陪伴页 kind=dance（邀请看舞蹈）。kind 原样透传给页面。
        recommendation.value = {
          kind: event.kind || 'module',
          key: event.module || event.emotion || '',
          reason: event.reason || '',
          emotion: event.emotion || '',
        }
        if (event.reason) pushMessage('action', `建议：${event.reason}`)
        break
      }
      case 'speech_started':
        // 抢话（barge-in）：只有数字人正在播报时才需要打断，否则会把
        // 同一轮的用户语音当成打扰而误清状态（旧实现无条件 interrupt，
        // 导致每句话开头都多打一次 /interrupt_talk，播报切换处有轻微卡顿）。
        if (speaking.value || busy.value) {
          interrupt('user')
        }
        busy.value = true
        speaking.value = false
        pipeline.value = 1
        connectionDetail.value = '正在听你说话…'
        break
      case 'speech_stopped':
        pipeline.value = 1
        connectionDetail.value = '正在识别你的语音…'
        break
      case 'pipeline':
        if (event.stage === 'asr') pipeline.value = 1
        if (event.stage === 'llm') pipeline.value = 2
        break
      case 'transcript': {
        // 同一轮可能补发更全的识别结果：一律覆盖待定用户气泡，不堆叠
        // （live 模式没有 response_done，索引在 response_accepted/interrupt 时清）
        if (userBubble >= 0) {
          messages.value[userBubble].content = event.text
        } else {
          userBubble = pushMessage('user', event.text)
        }
        busy.value = true
        pipeline.value = 2
        startSpeakingWatch()
        break
      }
      case 'assistant_delta':
        userBubble = -1
        if (assistantBubble < 0) assistantBubble = pushMessage('assistant', '')
        messages.value[assistantBubble].content += event.text
        break
      case 'audio_start':
        // 仅 default 模式会发；live 模式的“开口”由 /is_speaking 轮询捕捉
        pipeline.value = 3
        busy.value = false
        speaking.value = true
        startSpeakingWatch()
        connectionDetail.value = '数字人正在说话…'
        break
      case 'tts_fallback':
        pushMessage('action', '在线语音暂不可用，本段改用系统语音')
        break
      case 'action':
        pushMessage('action', `动作 · ${event.action}`)
        break
      case 'response_done':
        userBubble = -1
        assistantBubble = -1
        break
      case 'response_accepted':
        // live 模式：LLM 已生成完（语音/口型仍在播）——清掉气泡索引，
        // 否则下一轮的 transcript/assistant_delta 会接着往旧气泡里追加
        userBubble = -1
        assistantBubble = -1
        break
      case 'error':
        busy.value = false
        speaking.value = false
        pipeline.value = 0
        pushMessage('action', event.message || '处理失败')
        connectionDetail.value = event.message || '处理失败'
        break
    }
  }

  /**
   * live 模式的“说话中”监测：该模式不回传 audio_start/response_done 类事件，
   * 轮询 /is_speaking 捕捉“开口”和“说完”两个边沿，并驱动流程条第 4 步。
   */
  function startSpeakingWatch() {
    stopSpeakingWatch()
    let everSpoke = false
    let elapsed = 0
    speakingWatchTimer = setInterval(async () => {
      elapsed += 500
      if (!sessionId.value || elapsed > 120000) {
        speaking.value = false
        busy.value = false
        pipeline.value = 0
        stopSpeakingWatch()
        return
      }
      try {
        const data = await api('/is_speaking', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sessionid: sessionId.value }),
        })
        if (data?.data) {
          everSpoke = true
          speaking.value = true
          busy.value = false
          pipeline.value = 4 // 说话中：链路第 4 步（实时口型）点亮
          connectionDetail.value = '数字人正在说话…'
        } else if (everSpoke) {
          speaking.value = false
          busy.value = false
          pipeline.value = 0
          connectionDetail.value = '待命中，说说话或输入文字都可以'
          stopSpeakingWatch()
        }
      } catch {}
    }, 500)
  }

  function stopSpeakingWatch() {
    if (speakingWatchTimer) {
      clearInterval(speakingWatchTimer)
      speakingWatchTimer = null
    }
  }

  // ---------- 交互 ----------
  function sendText(text) {
    const content = (text || '').trim()
    if (!content || connection.value !== 'connected') return false
    if (ws && ws.readyState === 1) {
      // 新开一轮：上一轮的回复气泡已结束（WS 文字路径服务端不发 response_accepted，
      // 只能在这里清索引，否则新一轮的 assistant_delta 会接着往旧气泡里追加）
      assistantBubble = -1
      userBubble = pushMessage('user', content)
      ws.send(JSON.stringify({ type: 'text', text: content }))
      busy.value = true
      pipeline.value = 2
      startSpeakingWatch()
      return true
    }
    // WS 兜底：/human 也能让数字人开口（但没有流式文本回显）
    pushMessage('user', content)
    api('/human', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'chat', text: content, sessionid: sessionId.value, interrupt: true }),
    })
      .then(() => {
        busy.value = false
        speaking.value = true
        startSpeakingWatch()
      })
      .catch((err) => pushMessage('action', `发送失败：${err.message}`))
    return true
  }

  function interrupt(source = 'user') {
    const wasSpeaking = speaking.value || busy.value
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify({ type: 'cancel' }))
    }
    // 双保险：WS cancel 已能让服务端停手（_cancel_current_response），
    // 再打一发 /interrupt_talk（flush_talk + clear_buffer）确保 WebRTC 播报立即停。
    // 原版 LiveTalking 页面（web/app.js）也是两个通道一起发。
    if (sessionId.value && connection.value === 'connected') {
      api('/interrupt_talk', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sessionid: sessionId.value }),
      }).catch(() => {}) // 会话失效时静默，本地状态照常复位
    }
    stopSpeakingWatch()
    busy.value = false
    speaking.value = false
    pipeline.value = 0
    userBubble = -1
    assistantBubble = -1
    // 只有手动按钮打断且确实在播报时才提示，抢话（用户直接开口）静默
    if (source === 'button' && wasSpeaking) {
      connectionDetail.value = '已打断，我在听'
    }
  }

  async function ensureAudioContext() {
    if (!audioCtx || audioCtx.state === 'closed') {
      audioCtx = new AudioContext({ sampleRate: 16000 })
      workletReady = false
    }
    if (audioCtx.state === 'suspended') await audioCtx.resume()
    return audioCtx
  }

  async function toggleMic() {
    if (listening.value) {
      if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'cancel' }))
      listening.value = false
      stopMicTracks()
      connectionDetail.value = '麦克风已关闭'
      return
    }
    try {
      if (!ws || ws.readyState !== 1) openSocket()
      const context = await ensureAudioContext()
      micStream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
      })
      if (!workletReady) {
        const workletUrl = URL.createObjectURL(new Blob([WORKLET_SOURCE], { type: 'application/javascript' }))
        try {
          await context.audioWorklet.addModule(workletUrl)
          workletReady = true
        } finally {
          URL.revokeObjectURL(workletUrl)
        }
      }
      micSource = context.createMediaStreamSource(micStream)
      micNode = new AudioWorkletNode(context, 'livetalking-mic')
      micNode.port.onmessage = (message) => {
        if (ws && ws.readyState === 1) ws.send(message.data)
      }
      micSource.connect(micNode)
      // 零增益下沉：让 Worklet 被拉活，又不把麦克风声音放出来（避免回声）
      micSink = context.createGain()
      micSink.gain.value = 0
      micNode.connect(micSink)
      micSink.connect(context.destination)
      listening.value = true
      // 点麦克风是明确的对话意图：显式开闸，第一句不用再喊"你好"
      if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'awake' }))
      connectionDetail.value = '麦克风已开启，说完停顿约 0.85 秒自动识别'
    } catch (err) {
      pushMessage('action', `麦克风开启失败：${err.message || err}`)
    }
  }

  function stopMicTracks() {
    try {
      micNode?.disconnect()
      micSource?.disconnect()
      micSink?.disconnect()
      micStream?.getTracks().forEach((t) => t.stop())
    } catch {}
    micNode = null
    micSource = null
    micSink = null
    micStream = null
  }

  // ---------- 人设 / 素材 ----------
  async function loadPersonas() {
    try {
      const data = await api('/api/personas?mode=live')
      personas.value = data.personas || []
      activePersona.value = data.name || data.active || ''
      // 陪伴页只认「数字关怀助手」（2026-10-26 用户拍板：人设选择框删除）。
      // 引擎的全局 live 人设若停在别的档（如健康导览员），进页自动切回——
      // 导览页走连接级 persona=guide 覆盖，不受这个全局档影响。
      const care =
        personas.value.find((p) => p.id === 'care') ||
        personas.value.find((p) => p.name === '数字关怀助手')
      if (care && data.active && data.active !== care.id) {
        await switchPersona(care.id)
      }
    } catch {}
  }

  async function switchPersona(id) {
    try {
      const data = await api('/api/persona', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id }),
      })
      activePersona.value = data.name || id
      await loadPersonas()
    } catch (err) {
      pushMessage('action', `人设切换失败：${err.message}`)
    }
  }

  async function loadAssets() {
    try {
      assets.value = await api('/api/assets')
      scheduleAssetPolling()
    } catch {}
  }

  /** 生成轮询：有 running 任务时每 3 秒拉一次 /api/assets，任务结束自动停。 */
  function scheduleAssetPolling() {
    stopAssetPolling()
    const running = (assets.value?.generations || []).some((j) => j.status === 'running')
    if (!running) return
    assetPollTimer = setInterval(async () => {
      await loadAssets()
      if (!(assets.value?.generations || []).some((j) => j.status === 'running')) stopAssetPolling()
    }, 3000)
  }

  function stopAssetPolling() {
    if (assetPollTimer) {
      clearInterval(assetPollTimer)
      assetPollTimer = null
    }
  }

  /**
   * 一键生成新形象（LiveTalking 的「生成」按钮）：data/drop 里投递的视频 →
   * 后台人脸检测+裁剪（几分钟到几十分钟）。失败原因（如重名）行内提示。
   */
  async function generateAvatar(name) {
    try {
      const data = await apiDetail('/api/avatar/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      })
      pushMessage('action', data.message || `已开始生成人物 ${name}，完成后点「数字人」即可看到`)
      await loadAssets()
    } catch (err) {
      pushMessage('action', `生成失败：${err.message}`)
      loadAssets()
    }
  }

  /** 跳一段舞（仅当前形象段表带 dance 段时可用；服务端会先同步预加载，按钮转圈即可）。 */
  async function dance() {
    if (!sessionId.value || connection.value !== 'connected') return
    dancing.value = true
    try {
      const data = await apiDetail('/dance', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sessionid: sessionId.value }),
      })
      if (data && data.code !== undefined && data.code !== 0) {
        pushMessage('action', data.msg || '当前形象没有舞蹈段')
      }
    } catch (err) {
      pushMessage('action', `舞蹈启动失败：${err.message}`)
    } finally {
      dancing.value = false
    }
  }

  /** 断开当前 WebRTC+WS（供重连/重试共用）。 */
  function teardownConnection() {
    stopSpeakingWatch()
    try {
      ws?.close()
    } catch {}
    try {
      pc?.close()
    } catch {}
    pc = null
    ws = null
    userBubble = -1
    assistantBubble = -1
    if (videoEl) videoEl.srcObject = null
  }

  /** 连接失败/中断后手动重连（右栏「重试」按钮用）。 */
  async function retry() {
    if (connection.value === 'connecting') return
    teardownConnection()
    connectDetail = '正在重新建立实时通道…'
    try {
      await connect()
    } finally {
      connectDetail = ''
    }
  }

  /** 断开当前 WebRTC+WS，用指定形象重连（换“数字人”时用）。 */
  async function reconnect(avatarId, name) {
    teardownConnection()
    // 注意：不要把状态设成 connecting——connect() 开头有同名守卫会把自己拦回去
    connectDetail = `正在切换到 ${name || avatarId}，重建实时画面…`
    try {
      await connect()
    } finally {
      connectDetail = ''
    }
  }

  /**
   * 选择“数字人”预设：一项 = 形象 + 音色一对，两个一起换（与 LiveTalking 页面同规则）。
   * 音色走 /api/voice（下一句生效）；形象变了就带新 avatar 重连 WebRTC。
   */
  async function selectPreset(id) {
    const preset = (assets.value?.presets || []).find((p) => p.id === id)
    if (!preset || !preset.ready || busy.value) return
    const avatarChanged = preset.avatar !== (currentAvatar.value || assets.value?.defaultAvatar || '')
    const voiceChanged = preset.voice !== assets.value?.activeVoice
    if (!avatarChanged && !voiceChanged) return
    if (voiceChanged) {
      try {
        const data = await api('/api/voice', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: preset.voice }),
        })
        if (assets.value) assets.value.activeVoice = data.activeVoice || preset.voice
      } catch (err) {
        pushMessage('action', `音色切换失败：${err.message}`)
      }
    }
    if (avatarChanged) {
      currentAvatar.value = preset.avatar
      await reconnect(preset.avatar, preset.name)
    }
    await loadAssets()
  }

  return {
    // 状态
    connection,
    connectionTitle,
    connectionDetail,
    sessionId,
    serviceReady,
    listening,
    busy,
    speaking,
    emotion,
    recommendation,
    docCard,
    pipeline,
    messages,
    personas,
    activePersona,
    assets,
    currentAvatar,
    dancing,
    needPlayTap,
    // 方法
    init,
    connect,
    tapPlay,
    sendText,
    interrupt,
    toggleMic,
    switchPersona,
    selectPreset,
    retry,
    loadAssets,
    clearance: () => { recommendation.value = null },
    generateAvatar,
    dance,
  }
}
