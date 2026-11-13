import { onBeforeUnmount, ref, watch } from 'vue'

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
  const currentSegment = ref('')   // 当前播放片段名（idle1/greet/X_to_Y），待机框展示
  const serviceReady = ref(false)
  const listening = ref(false)
  // 实时输入音量（RMS 归一化 0~1）：麦克风按钮旁的电平条用。
  // 没有它用户无法判断"到底听到我没有"——只有亮/灭的按钮说明不了链路死活
  // （2026-11-13 补齐，与导览页同一实现）
  const micLevel = ref(0)
  // 上行诊断（2026-11-13）：麦克风电平条只证明"麦克风有输入"，不证明 PCM
  // 真的发出去了（AnalyserNode 挂在本地音频图上，与 WebSocket 无关）。
  // 这两个计数把链路切成两段，一眼看出断在哪：
  //   uplinkBytes 一直不涨 = PCM 没上行（ws 未连/发送被拦）
  //   uplinkBytes 涨但 engineEvents 不涨 = 引擎没回任何事件（VAD/ASR 未触发）
  const uplinkBytes = ref(0)
  const engineEvents = ref(0)
  // 麦克风常开意图（2026-11-11 用户要求"麦克风要一直打开"）：进入页面自动
  // 开麦、断线自动重连补开、AudioContext 被浏览器挂起时等第一次点击恢复；
  // 用户手动点麦克风关闭才置 false，此后不再自动恢复
  let micDesired = false
  let awakeActive = false        // 唤醒闸是否被显式打开（手动点麦克风）
  let everWoke = false           // 引擎是否确认过唤醒（wake 事件）：configure 补发时据此重开闸
  let reconnectTimer = null
  let reconnectAttempts = 0
  let gestureResumeArmed = false

  // sessionId 从空变就绪（/offer 返回）时，重发 configure 把麦克风连接绑到
  // 真实会话；若此前已显式唤醒，configure 会重新武装唤醒门，补一声 awake
  watch(sessionId, (sid) => {
    if (!sid || ws?.readyState !== 1) return
    ws.send(JSON.stringify({ type: 'configure', mode: 'live', sessionid: sid }))
    // 补偿条件必须包含 everWoke（2026-11-13 修 P2-6）：引擎 _handle_text 的
    // configure 分支会无条件 self.wake.reset(mode)，而 awakeActive 只在**手动点
    // 麦克风**时为真——语音喊「你好」唤醒的用户不满足 awakeActive，补发
    // configure 后门被悄悄关上，表现为"我刚才明明喊过你好，之后说话又没反应"。
    if (listening.value && (awakeActive || everWoke)) {
      ws.send(JSON.stringify({ type: 'awake' }))
    }
  })
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
  const voiceSwitchOpen = ref(false)  // 音色手动切换面板（唯一破坏配对的入口）
  const currentAvatar = ref('') // 当前选择的形象 id（/offer 时带给服务端）
  const dancing = ref(false) // 舞蹈预加载中
  const needPlayTap = ref(false)
  // 切换数字人中（2026-11-13）：重建 WebRTC 期间视频是黑的，给页面一个
  // 明确的"切换中"遮罩文案，别让用户以为坏了
  const switching = ref(false)

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
  let micOpening = null // 开麦进行中的 Promise：合并并发调用，防止双路上行
  let micOpeningAwake = false // 合并期间是否有人请求显式开闸（手动点麦克风）
  let micAnalyser = null // 音量电平分析器（诊断 + UI 电平条）
  let micLevelBuf = null
  let micLevelTimer = null
  let userBubble = null // 待回写的用户气泡对象（截断会话后依然有效）
  let assistantBubble = null
  let disposed = false
  let readinessTimer = null
  let speakingWatchTimer = null
  let connectDetail = '' // 连接中的自定义说明（切换数字人时用）
  let assetPollTimer = null // 素材生成轮询
  let segmentTimer = null   // 当前播放片段轮询（待机框展示）
  let personaId = '' // 连接级人设（导览页传 guide：引擎侧人设+情绪推荐角色都会按它分工）

  onBeforeUnmount(teardown)

  function teardown() {
    disposed = true
    stopReadinessPolling()
    stopSpeakingWatch()
    stopAssetPolling()
    stopSegmentPolling()
    stopAutoTryPlay()
    stopCtxKeepAlive()
    try {
      ws?.close()
    } catch {}
    pc?.close()
    pc = null
    ws = null
    stopMicTracks()
    // AudioContext 必须显式关闭：Chrome 硬件上下文上限约 6 个，每进一次页面
    // 都新建一个不关的话，进出六七次之后 new AudioContext 直接抛错，
    // 麦克风永久"开启失败"（2026-11-13 修复）
    if (audioCtx && audioCtx.state !== 'closed') {
      audioCtx.close().catch(() => {})
    }
    audioCtx = null
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
      startCtxKeepAlive() // AudioContext 挂起自愈看门狗（随页面存活）
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
        if (!pc) return
        if (pc.connectionState === 'failed' || pc.connectionState === 'closed') {
          setConnection('failed', '连接已中断', 'WebRTC 通道断开，请重试')
        } else if (pc.connectionState === 'disconnected' && connection.value === 'connected') {
          // disconnected 多是瞬时的网络波动，不翻脸，只换文案
          connectionDetail.value = '网络波动，画面通道恢复中…'
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
      // 通道建成即 connected（2026-11-13 修复）：WebSocket 随后就开，文字对话
      // 立即可用。以前要等首帧才切 connected 才开 WS，引擎冷启动的半分钟里
      // 输入框整个禁着——而那时麦克风链路其实已经能说话。
      setConnection('connected', '实时数字人已连接', '口型与语音同步直播中')
      startAutoTryPlay()
      openSocket()
      loadPersonas()
      loadAssets()
      startSegmentPolling()
      // 首帧不到只降级提示，不再把连接打成 failed（2026-11-12 的教训：
      // 引擎冷启动帧来得晚，拆连接会让"点击开播"和等待超时叠在一起）。
      // 代际守卫（2026-11-13 DeepSeek 审查）：回调里比对发起时的 pc 引用——
      // 用户在 40s 等待期间切换数字人会重建 pc，旧定时器不能覆写新连接的文案
      const frameWatchPc = pc
      void waitForFirstFrame(video).catch(async () => {
        if (disposed || connection.value !== 'connected' || pc !== frameWatchPc) return
        connectionDetail.value = '画面准备中（引擎可能正在加载，对话不受影响）…'
        try {
          await waitForFirstFrame(video, 40000)
          if (!disposed && connection.value === 'connected' && pc === frameWatchPc) {
            connectionDetail.value = '口型与语音同步直播中'
          }
        } catch {
          if (!disposed && connection.value === 'connected' && pc === frameWatchPc) {
            needPlayTap.value = false
            connectionDetail.value = '画面迟迟未到，语音对话不受影响；可点「重试连接」重建画面'
          }
        }
      })
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
        stopAutoTryPlay()
      })
      .catch(() => {})
  }

  // connected 后浏览器仍可能拦自动播放：后台定时补 play()，用户随意点一下
  // 页面（手势）也会解锁——不依赖那个可能被遮住的"点击开播"按钮
  let playRetryTimer = null
  function startAutoTryPlay() {
    stopAutoTryPlay()
    let n = 0
    playRetryTimer = setInterval(() => {
      n += 1
      if (!needPlayTap.value) return stopAutoTryPlay()
      videoEl?.play().then(() => {
        needPlayTap.value = false
        stopAutoTryPlay()
      }).catch(() => {})
      if (n >= 12) stopAutoTryPlay()
    }, 2500)
  }
  function stopAutoTryPlay() {
    if (playRetryTimer) {
      clearInterval(playRetryTimer)
      playRetryTimer = null
    }
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
      // 麦克风常开：用户意图仍要麦克风时自动重连（退避 0.8s→5s），
      // configure 完成后 ensureMicOpen 会补开麦克风并重新唤醒
      if (micDesired) {
        // 掉线恢复要把状态拨回 connecting：以前 error 直接把 UI 打成
        // failed，而 close 又会自动重连成功——麦克风明明能用了，画面还被
        // "通话通道断开"的遮罩盖着，必须手点重试（2026-11-13 修复）
        if (connection.value !== 'connecting') {
          setConnection('connecting', '正在恢复对话通道', '连接波动，自动重连中…')
        }
        clearTimeout(reconnectTimer)
        reconnectTimer = setTimeout(() => {
          reconnectTimer = null
          if (micDesired && (!ws || ws.readyState > 1)) openSocket()
        }, Math.min(800 * ++reconnectAttempts, 5000))
      } else if (connection.value === 'connected') {
        // 用户主动关麦后的掉线：WebRTC 画面还在，保留 connected 只提示
        connectionDetail.value = '对话通道已断开（麦克风未开），点麦克风按钮恢复'
      }
    })
    ws.addEventListener('error', () => {
      // error 之后必然紧跟 close：不在这里翻脸，交给 close 的重连逻辑，
      // 否则自动重连成功后 UI 永远停在"通话通道断开"
      if (connection.value === 'connected') {
        connectionDetail.value = '对话通道波动，正在自动恢复…'
      }
    })
  }

  function onSocketMessage(message) {
    if (typeof message.data === 'string') {
      engineEvents.value += 1
      try {
        handleEvent(JSON.parse(message.data))
      } catch {}
      return
    }
    // 二进制 PCM 只在 default 模式（浏览器本地播放）使用；实时模式语音走 WebRTC，忽略
  }

  function pushMessage(role, content) {
    const message = { role, content }
    messages.value.push(message)
    // 超长会话从头截断：存消息对象引用而不是下标，截断后旧索引会整体前移
    // 导致转写写进别人的气泡（2026-11-13 修复）
    if (messages.value.length > 200) messages.value.splice(0, messages.value.length - 200)
    return message
  }

  function handleEvent(event) {
    switch (event.type) {
      case 'configured':
        // 麦克风常开：通道就绪后若用户意图是开着麦克风，自动补开并重新唤醒
        // （覆盖首次自动开麦、断线重连、AudioContext 恢复三种路径）
        reconnectAttempts = 0
        // 掉线恢复完成：WS 重新就绪即把连接态拨回 connected
        // （WebRTC 画面通道整个掉线时才需要用户点重试）
        if (connection.value === 'connecting' && sessionId.value && pc) {
          setConnection('connected', '实时数字人已连接', '对话通道已恢复')
        }
        if (micDesired && !listening.value) void ensureMicOpen(false)
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
        everWoke = true
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
        if (userBubble) {
          userBubble.content = event.text
        } else {
          userBubble = pushMessage('user', event.text)
        }
        busy.value = true
        pipeline.value = 2
        startSpeakingWatch()
        break
      }
      case 'assistant_delta':
        userBubble = null
        if (!assistantBubble) assistantBubble = pushMessage('assistant', '')
        assistantBubble.content += event.text
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
        userBubble = null
        assistantBubble = null
        break
      case 'response_accepted':
        // live 模式：LLM 已生成完（语音/口型仍在播）——清掉气泡索引，
        // 否则下一轮的 transcript/assistant_delta 会接着往旧气泡里追加
        userBubble = null
        assistantBubble = null
        break
      case 'session_reset':
        // 引擎软重置（用户说"重新开始"，或一句裸"你好"触发软重置）：
        // 以前没有这个 case——用户气泡推上去了却永远等不到回复，画面像卡死，
        // 情绪徽章/推荐卡/资料卡也全部残留（2026-11-13 补齐，与导览页同契约）。
        // 说话/思考状态必须一起复位：否则 speakingWatch 继续轮询一个已重置的
        // 会话，UI 卡在"说话中"直到超时（DeepSeek 审查 P0-4）
        stopSpeakingWatch()
        busy.value = false
        speaking.value = false
        pipeline.value = 0
        messages.value = []
        userBubble = null
        assistantBubble = null
        emotion.value = null
        recommendation.value = null
        docCard.value = null
        pushMessage('action', '会话已重新开始，我们重新聊')
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
      assistantBubble = null
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
    userBubble = null
    assistantBubble = null
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

  /** 音量电平条：150ms 读一次时域 RMS（与导览页同一算法，×12 提升观感灵敏度）。 */
  function startMicLevelMeter() {
    stopMicLevelMeter()
    if (!micAnalyser || !micLevelBuf) return
    micLevelTimer = setInterval(() => {
      if (disposed || !micAnalyser) return
      if (!listening.value) {
        micLevel.value = 0
        return
      }
      try {
        micAnalyser.getFloatTimeDomainData(micLevelBuf)
      } catch {
        return
      }
      let sum = 0
      for (let i = 0; i < micLevelBuf.length; i += 1) sum += micLevelBuf[i] * micLevelBuf[i]
      micLevel.value = Math.min(1, Math.sqrt(sum / micLevelBuf.length) * 12)
    }, 150)
  }

  function stopMicLevelMeter() {
    if (micLevelTimer) {
      clearInterval(micLevelTimer)
      micLevelTimer = null
    }
    micLevel.value = 0
  }

  // AudioContext 是否被浏览器挂起（采音停了但 UI 不知道）。
  // 刷新页面/多标签切换后 Chrome 会挂起新上下文，麦克风权限有、流也开着，
  // 但采样不跑——用户看到"音量 0%、检测不到"，却没有任何提示（2026-11-13 修复）。
  const audioBlocked = ref(false)

  function armGestureResume() {
    // 浏览器策略：无用户手势时 AudioContext 保持 suspended（麦克风已开
    // 但采样不跑）。等点击/按键恢复采样，micDesired 不变。
    // 常驻武装（2026-11-13 改）：旧实现一次性用完就移除监听，页面刷新后
    // 挂起的上下文等不到那个"第一次点击"——保留监听，每次都尝试恢复。
    if (gestureResumeArmed) return
    gestureResumeArmed = true
    const handler = () => {
      if (disposed) return
      if (micDesired && audioCtx && audioCtx.state === 'suspended') {
        audioCtx.resume().catch(() => {})
      }
      // 不 removeEventListener：浏览器可能在页面生命周期内多次挂起上下文
    }
    window.addEventListener('pointerdown', handler)
    window.addEventListener('keydown', handler)
  }

  // AudioContext 挂起自愈（2026-11-13）：页面切后台再回来时浏览器会把
  // AudioContext 挂起，listening 还是 true、麦克风灯亮着，但采样早停了——
  // 用户说话毫无反应。旧实现只靠"第一次点击"恢复，一次性用完了就不再武装。
  // 这里照导览页的做法起一个低频看门狗，发现 suspended 就尝试 resume。
  let ctxKeepAliveTimer = null
  function startCtxKeepAlive() {
    if (ctxKeepAliveTimer) return
    ctxKeepAliveTimer = setInterval(() => {
      if (disposed || !audioCtx || audioCtx.state === 'closed') {
        audioBlocked.value = false
        return
      }
      if (audioCtx.state === 'suspended' && micDesired) {
        // 挂起了：先尝试自己恢复（部分浏览器允许），同步置提示位
        audioCtx.resume().catch(() => {})
        audioBlocked.value = listening.value
      } else {
        audioBlocked.value = false
      }
    }, 1000)
  }
  function stopCtxKeepAlive() {
    if (ctxKeepAliveTimer) {
      clearInterval(ctxKeepAliveTimer)
      ctxKeepAliveTimer = null
    }
  }

  async function ensureMicOpen(openAwake = false) {
    if (listening.value) return
    // 并发合并（2026-11-12/13 修复）：进页面时 openMic() 与 WS configured 回包
    // 几乎必然并发，旧的"先判断后 await"守卫两个调用都能穿过——结果双路 PCM
    // 同时上行（服务端 VAD 时序全乱），且第二份流覆盖引用后第一份永远不关
    // （UI 显示关麦、轨道仍在收音）。
    if (micOpening) {
      // 合并期间有人手动点麦克风：记住"要显式开闸"，不能把 openAwake 丢掉
      // （否则自动开麦在途时用户点麦克风，第一句还得喊"你好"）
      if (openAwake) micOpeningAwake = true
      return micOpening
    }
    micOpening = (async () => {
      micOpeningAwake = !!openAwake
      try {
        if (!ws || ws.readyState !== 1) openSocket()
        const context = await ensureAudioContext()
        armGestureResume()
        micStream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
        })
        if (disposed) {
          micStream.getTracks().forEach((t) => t.stop())
          return
        }
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
        // 音量电平分析器：只做诊断/UI 显示，不参与上行链路
        try {
          micAnalyser = context.createAnalyser()
          micAnalyser.fftSize = 512
          micLevelBuf = new Float32Array(micAnalyser.fftSize)
          micSource.connect(micAnalyser)
        } catch {
          micAnalyser = null
          micLevelBuf = null
        }
        micNode = new AudioWorkletNode(context, 'livetalking-mic')
        micNode.port.onmessage = (message) => {
          if (ws && ws.readyState === 1) {
            ws.send(message.data)
            uplinkBytes.value += message.data.byteLength || 0
          }
        }
        micSource.connect(micNode)
        // 零增益下沉：让 Worklet 被拉活，又不把麦克风声音放出来（避免回声）
        micSink = context.createGain()
        micSink.gain.value = 0
        micNode.connect(micSink)
        micSink.connect(context.destination)
        listening.value = true
        startMicLevelMeter()
        // 手动点麦克风＝明确的对话意图：显式开闸，第一句不用喊"你好"；
        // 进页面自动开麦则保持唤醒门武装（2026-11-11 用户要求"激活词也是你好"），
        // 听到「你好」才进入对话——与检测主页行为一致
        awakeActive = micOpeningAwake
        if (micOpeningAwake && ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'awake' }))
        connectionDetail.value = micOpeningAwake
          ? '麦克风已开启，说完停顿约 0.85 秒自动识别'
          : '麦克风已开启，说「你好」唤醒数字人'
      } catch (err) {
        if (!disposed) pushMessage('action', `麦克风开启失败：${err.message || err}`)
      } finally {
        micOpening = null
      }
    })()
    return micOpening
  }

  async function toggleMic() {
    if (listening.value) {
      micDesired = false
      awakeActive = false
      if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'cancel' }))
      listening.value = false
      stopMicTracks()
      connectionDetail.value = '麦克风已关闭'
      return
    }
    micDesired = true
    await ensureMicOpen(true)
  }

  // 进页面自动开麦（CompanionDetect onMounted 调用）：麦克风一直打开的入口，
  // 不开唤醒闸——「你好」仍是激活词
  async function openMic() {
    micDesired = true
    await ensureMicOpen(false)
  }

  function stopMicTracks() {
    stopMicLevelMeter()
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
    micAnalyser = null
    micLevelBuf = null
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

  /** 轮询当前播放片段（引擎状态机），供待机框展示播放逻辑。 */
  async function pollSegment() {
    if (!sessionId.value) return
    try {
      const res = await fetch(BASE + '/api/session/segment?sessionid=' + encodeURIComponent(sessionId.value))
      const body = await res.json()
      if (body.code === 0 && body.data) currentSegment.value = body.data.segment
    } catch {}
  }

  function startSegmentPolling() {
    stopSegmentPolling()
    pollSegment()
    segmentTimer = setInterval(pollSegment, 800)
  }
  function stopSegmentPolling() {
    if (segmentTimer) { clearInterval(segmentTimer); segmentTimer = null }
    currentSegment.value = ''
  }

  async function loadAssets() {
    try {
      assets.value = await api('/api/assets')
      scheduleAssetPolling()
      // 人物↔音色强绑定：每次刷新列表后校正一次（引擎重启/他处改音色都会被拨回）
      void syncVoiceToAvatar()
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

  /**
   * 删除一个数字人（引擎 /api/avatar/delete：素材目录 + presets 注册一起清）。
   * 内置人物（zzf/yy 素材）与正在使用的形象由引擎侧拒绝并返回原因。
   */
  async function deleteAvatar(avatarId, name) {
    try {
      const data = await apiDetail('/api/avatar/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: avatarId }),
      })
      pushMessage('action', data.message || `已删除人物 ${name || avatarId}`)
      // 删的是当前形象 → 立刻切回默认形象重连，别停在已删除的素材上
      if (currentAvatar.value === avatarId) {
        currentAvatar.value = assets.value?.defaultAvatar || ''
        // 显示名查预设表，别把 ASCII id 当名字露给用户（DeepSeek 审查 P2-10）
        const fallback = (assets.value?.presets || []).find(
          (p) => p.avatar === currentAvatar.value,
        )
        await reconnect(currentAvatar.value, fallback?.name || '默认形象')
      }
      await loadAssets()
      return true
    } catch (err) {
      pushMessage('action', `删除失败：${err.message}`)
      return false
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
    stopAutoTryPlay()
    try {
      ws?.close()
    } catch {}
    try {
      pc?.close()
    } catch {}
    pc = null
    ws = null
    userBubble = null
    assistantBubble = null
    if (videoEl) videoEl.srcObject = null
  }

  /** 连接失败/中断后手动重连（右栏「重试」按钮用）。 */
  async function retry() {
    if (connection.value === 'connecting') return
    teardownConnection()
    connectDetail = '正在重新建立实时通道…'
    switching.value = true
    try {
      await connect()
    } finally {
      connectDetail = ''
      switching.value = false
    }
  }

  /** 断开当前 WebRTC+WS，用指定形象重连（换“数字人”时用）。 */
  async function reconnect(avatarId, name) {
    // 注意：不要把状态设成 connecting——connect() 开头有同名守卫会把自己拦回去；
    // 用 switching 标记切换中，页面据此显示"正在切换到 X"遮罩（2026-11-13）
    switching.value = true
    teardownConnection()
    connectDetail = `正在切换到 ${name || avatarId}，重建实时画面…`
    try {
      await connect()
    } finally {
      connectDetail = ''
      switching.value = false
    }
  }

  /**
   * 选择“数字人”预设：一项 = 形象 + 音色一对，两个一起换（与 LiveTalking 页面同规则）。
   * 音色走 /api/voice（下一句生效）；形象变了就带新 avatar 重连 WebRTC。
   */
  /** 人物→音色强绑定（2026-11-12 用户要求）：
   *  每个形象只认它配对表里的音色（zzf→zzf音色、yy→yy音色、照片数字人→
   *  生成时选的音色）。发现形象与音色不成对时，自动把引擎音色拨回配对值，
   *  并给出提示——只有用户显式点「切换音色」才允许打破配对。 */
  let manualVoice = ''   // 用户手动指定过的音色（唯一允许破坏配对的途径）
  async function syncVoiceToAvatar() {
    const avatar = currentAvatar.value || assets.value?.defaultAvatar || ''
    if (!avatar) return
    const pair = (assets.value?.presets || []).find((p) => p.avatar === avatar && p.ready)
    if (!pair) return
    if (manualVoice && manualVoice === assets.value?.activeVoice) return
    if (assets.value?.activeVoice === pair.voice) {
      manualVoice = ''
      return
    }
    try {
      const data = await api('/api/voice', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: pair.voice }),
      })
      if (assets.value) assets.value.activeVoice = data.activeVoice || pair.voice
      manualVoice = ''
      pushMessage('action', `音色已按配对自动切到「${pair.voice}」（形象与音色强绑定）`)
    } catch (err) {
      pushMessage('action', `音色同步失败：${err.message}`)
    }
  }

  /** 手动切换音色（唯一允许打破"人物↔音色"配对的入口）。 */
  async function switchVoice(id) {
    if (!id) return
    try {
      const data = await api('/api/voice', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id }),
      })
      if (assets.value) assets.value.activeVoice = data.activeVoice || id
      manualVoice = id
      const name = (assets.value?.voices || []).find((v) => v.id === id)?.name || id
      pushMessage('action', `已手动切换音色到「${name}」（与当前形象的默认配对不同）`)
      // 面板保持展开：切完要让用户看到"✓ 使用中"的确认反馈。
      // 旧代码写的是 `if (voiceSwitchOpen) voiceSwitchOpen.value = false`——
      // ref 对象恒为真，面板一点击就自动收起，确认永远看不到（2026-11-13 修复）
    } catch (err) {
      pushMessage('action', `音色切换失败：${err.message}`)
    }
  }

  async function selectPreset(id) {
    const preset = (assets.value?.presets || []).find((p) => p.id === id)
    // busy 不再拦截：切换是用户明确意图（调用方已先 interrupt 打断在播回复）
    if (!preset || !preset.ready) return
    const avatarChanged = preset.avatar !== (currentAvatar.value || assets.value?.defaultAvatar || '')
    const voiceChanged = preset.voice !== assets.value?.activeVoice
    if (!avatarChanged && !voiceChanged) return
    // 选人物=选一对：音色强制跟到该人物的绑定音色（手动覆盖记录清零）
    manualVoice = ''
    if (avatarChanged) {
      currentAvatar.value = preset.avatar
      await reconnect(preset.avatar, preset.name)
    }
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
    await loadAssets()
  }

  return {
    // 状态
    connection,
    connectionTitle,
    connectionDetail,
    sessionId,
    currentSegment,
    serviceReady,
    listening,
    micLevel,
    uplinkBytes,
    engineEvents,
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
    switching,
    audioBlocked,
    // 方法
    init,
    connect,
    tapPlay,
    sendText,
    interrupt,
    toggleMic,
    openMic,
    switchPersona,
    selectPreset,
    switchVoice,
    voiceSwitchOpen,
    syncVoiceToAvatar,
    retry,
    loadAssets,
    clearance: () => { recommendation.value = null },
    generateAvatar,
    deleteAvatar,
    dance,
  }
}
