import { getCurrentInstance, onBeforeUnmount, ref } from 'vue'

/**
 * 人体健康监测中心（首页导览页）专属的实时语音客户端。
 *
 * 传输架构（2026-10-24 用户拍板：此页不需要 wav2lip，画面只靠前端动画段播放）：
 * 一条 WebSocket 搞定收发——麦克风 PCM 上行、回复文本事件下行、回复语音
 * 以二进制 PCM（16kHz mono Int16，0x01 头）下行，浏览器 AudioContext 本地
 * 排队播放。不再建立 WebRTC / 不占用引擎的 Wav2Lip 渲染管线，省掉
 * "TTS→渲染→帧同步→WebRTC"约 1.2s 的出声延迟，引擎也不为这页渲染任何帧。
 *
 * configure：mode=default + persona=guide + wake_word=暖暖——引擎侧
 * default 链路按 guide 人设选暖暖话术与 guide_boy_v1 音色、发 [DOC:] 资料卡、
 * 跑模块分诊推荐（与 live 链路同源）。
 *
 * 说话状态（驱动动画数字人）：talking 在 transcript 受理即置真（LLM+TTS 的
 * 首音延迟当说话链过渡的跑道），audio_start/response_done + 本地播放队列
 * 排空决定收口——不再轮询 /is_speaking（default 模式没有 avatar session）。
 */

const BASE = (import.meta.env.VITE_LIVETALKING_URL || 'http://127.0.0.1:8020').replace(/\/+$/, '')

// 页面身份令牌（每次整页加载生成一次）：引擎仲裁用——带令牌的新版页面
// 永远优先于不带令牌的旧版僵尸页面（旧代码页面会在引擎重启后重连并抢走
// 麦克风，把回复发到无人观看的隐形页面上，用户听不到＝"你好没用"）
const PAGE_TOKEN = 'pt-' + Math.random().toString(36).slice(2, 10)

// 麦克风重采样 Worklet：任意采样率 → 16kHz Int16，512 帧一包（与引擎协议一致）
const WORKLET_SOURCE = `
class HomeGuideMic extends AudioWorkletProcessor {
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
registerProcessor("home-guide-mic", HomeGuideMic);
`

let sharedInstance = null

/**
 * 首页导览客户端是**模块级单例**（2026-10-24 用户拍板"从其他页面回来要继承
 * 上一次的会话"）：SPA 离开首页只停麦（suspendForNavigation），WebSocket 与
 * 引擎侧会话记忆（多轮记忆/情绪/推荐状态）原样保留；回到首页重新开麦即续聊。
 * 真正的销毁只发生在页面卸载（beforeunload）。
 */
export function useHomeGuide() {
  if (!sharedInstance) sharedInstance = createHomeGuide()
  const inst = sharedInstance
  // 每个挂载的组件注册自己的卸载钩子：离开首页＝停麦，不销毁会话
  if (getCurrentInstance()) {
    onBeforeUnmount(() => inst.suspendForNavigation())
  }
  return inst
}

function createHomeGuide() {
  // ---------- 对外状态 ----------
  const connection = ref('idle') // idle | connecting | connected | failed
  const connectionDetail = ref('暖暖正在醒来，请稍等…')
  const serviceReady = ref(false)
  const listening = ref(false)
  const busy = ref(false)
  const speaking = ref(false)
  // 口型动画驱动（比 speaking 早一个身位）：转写一受理（transcript）就置真，
  // 让动画数字人立刻进 idle→talk 说话链——LLM+TTS 的首音延迟（约 2 秒）
  // 正好当过渡段的跑道，嘴与语音基本同时动。
  const talking = ref(false)
  // 输出被浏览器自动播放策略压住时置真：点一下页面即解锁
  const needPlayTap = ref(false)
  // 引擎麦克风仲裁判定本窗口让出后置真：自动补开麦的一切路径（点击解锁/
  // 切回标签页）都不再抢开，直到本页重连（刷新）重新成为持有者
  const micYielded = ref(false)
  // 2026-10-25 关键修复：本闭包已被更新的连接取代（HMR 热更新/重复挂载会
  // 留下旧闭包，其断线重连会不断新建连接抢走持有权——可见页面从此收不到
  // wake/跳转事件，即"说了你好没用/跳转失败"的根因）。被取代后彻底退场：
  // 关闭 socket、断线不再重连、看门狗/自动重启全部停手。
  let superseded = false
  // 情绪检测（引擎逐句分类）：{ key, label, changed, consecutive, hint }
  const emotion = ref(null)
  // 模块推荐（连续两句同负向情绪；本页只认 kind=module，跳舞邀请属于陪伴页）
  const recommendation = ref(null)
  // 资料卡：只在 LLM 回复带教学/说明意义（显式 [DOC:] 标签）时才有内容
  const docCard = ref(null)
  // 最近一条回复全文（页面可选用；不渲染聊天气泡）
  const lastReply = ref('')
  // 最近听到的语音识别原话（含未唤醒被拦的，调试常听用）
  const lastHeard = ref('')
  // 唤醒状态（2026-10-25）：听到「你好」之前，识别面板保持安静——
  // 未唤醒拦截/没听清等提示一律不进日志。session_reset 会先归 false，
  // 软重置路径随后到达的 wake 事件再置回 true，顺序与引擎一致。
  const awake = ref(false)
  // 识别记录（时间+类型+原文，右栏识别面板用；50 条封顶）
  const heardLog = ref([])
  function pushHeard(kind, text) {
    // 唤醒前不出声：面板不显示任何提示/拦截记录（用户要求，听到你好再开始）
    if (!awake.value) return
    const now = new Date()
    const hh = String(now.getHours()).padStart(2, '0')
    const mm = String(now.getMinutes()).padStart(2, '0')
    const ss = String(now.getSeconds()).padStart(2, '0')
    heardLog.value.push({ t: `${hh}:${mm}:${ss}`, kind, text: String(text || '').slice(0, 60) })
    if (heardLog.value.length > 50) heardLog.value.splice(0, heardLog.value.length - 50)
  }
  // 麦克风设备名与实时输入音量（0~1，面板诊断用）
  const micLabel = ref('')
  const micLevel = ref(0)
  let micAnalyser = null
  let micLevelBuf = null
  // 导览页语音确认跳转：引擎判定用户在肯定模块推荐时发出（页面据此 router.push）
  const moduleJump = ref(null)
  // 刷新口令/新用户接入：引擎通知前端重置 greet 与卡片
  const sessionReset = ref(0)

  // ---------- 内部句柄 ----------
  let ws = null
  // socket 代数：每次新建 WS 自增。让位（mic_taken）之后只允许**新建 socket**
  // 上的 configured 复活本页；老 socket 上紧跟 mic_taken 到达的 configured 一律
  // 视为引擎的拒绝回执，不得清让位标记、不得重新开麦——否则被取代的页面每轮
  // 重连都抢一次物理麦克风，把现任页面的 getUserMedia 挤成永久失败
  // （2026-10-25 多页面互抢麦克风事故的根因之一）
  let socketGen = 0
  let yieldedAtSocketGen = 0
  let audioCtx = null
  let micSource = null
  let micNode = null
  let micSink = null
  let micStream = null
  let workletReady = false
  let micBusy = false // 开麦进行中：防连点把开/关交错打死
  let autoListenActive = false // autoListen 重试循环单飞标记：防止叠出并发循环
  let lastPcmAt = 0 // 最后一次 worklet 出 PCM 的时刻（采音健康看门狗用）
  let micRestarts = 0 // 本次页面生命周期内自动重启麦克风的次数（封顶 5）
  let emptyStreak = 0 // 连续"没听清"次数：连续 3 次自动重启采音（浏览器采音链路偶发坏死的自愈）
  let ctxKeepAliveTimer = 0 // 音频上下文挂起自愈定时器
  let disposed = false
  // 首页当前是否在前台（SPA 导航离开时置假：停麦、禁止自动补开；
  // WS 与引擎会话保留，回来续聊）
  let pageActive = true
  let readinessTimer = null
  let reconnectTimer = null
  let booted = false
  let unlockBound = false
  // 回复语音本地播放队列（AudioContext 顺序排播）
  let nextStart = 0
  let drainTimer = null
  let drainToken = 0
  const playSources = new Set()
  let pendingPcm = []
  // 话轮悬死看门狗：busy/talking 挂起且 45s 内没有任何 WS 消息（LLM 断网挂死、
  // 话轮收尾事件丢失等——2026-10-25 实测"正在听你说话"永久卡死）→ 主动发
  // cancel（引擎抬代际作废僵尸输出）并收口本页状态，任何环节挂死都能自愈
  let lastWsActivityAt = Date.now()
  let turnWatchTimer = 0

  // 真正的销毁只在页面卸载（SPA 导航走 suspendForNavigation，不动会话）
  if (typeof window !== 'undefined') {
    window.addEventListener('beforeunload', teardown)
    turnWatchTimer = setInterval(() => {
      if (disposed) return
      if (!(busy.value || talking.value)) return
      if (Date.now() - lastWsActivityAt < 45000) return
      lastWsActivityAt = Date.now()
      console.warn('[HomeGuide] 话轮看门狗：45s 无任何事件，自动收口')
      try {
        ws?.send(JSON.stringify({ type: 'cancel' }))
      } catch {}
      busy.value = false
      talking.value = false
      if (!speaking.value) connectionDetail.value = '刚才好像卡了一下，请再说一次'
    }, 2000)
  }

  function teardown() {
    disposed = true
    stopReadinessPolling()
    clearInterval(turnWatchTimer)
    clearTimeout(reconnectTimer)
    clearTimeout(drainTimer)
    try {
      ws?.close()
    } catch {}
    ws = null
    flushStreamPlayback()
    stopMicTracks()
    if (audioCtx && audioCtx.state !== 'closed') {
      try {
        audioCtx.close()
      } catch {}
      audioCtx = null
    }
  }

  /** SPA 离开首页：只停麦（别的页面不该被监听），WebSocket 与引擎侧
   *  会话记忆保留——回来 init() 重新开麦即继承上次对话。 */
  function suspendForNavigation() {
    pageActive = false
    if (listening.value) {
      listening.value = false
      stopMicTracks()
    }
  }

  async function api(path, options) {
    const res = await fetch(BASE + path, options)
    if (!res.ok) throw new Error(`${path} → HTTP ${res.status}`)
    return res.json()
  }

  // ---------- 服务就绪探测（模型加载约 10~30 秒，冷启动更久） ----------
  function stopReadinessPolling() {
    if (readinessTimer) {
      clearInterval(readinessTimer)
      readinessTimer = null
    }
  }

  async function checkReadiness() {
    const canWrite = connection.value === 'idle'
    try {
      const data = await api('/api/readiness')
      serviceReady.value = Boolean(data.ready)
      if (data.ready) {
        if (canWrite) connectionDetail.value = '暖暖马上就好…'
        return true
      }
      if (canWrite) connectionDetail.value = '暖暖正在醒来，请稍等…'
    } catch {
      serviceReady.value = false
      if (canWrite) connectionDetail.value = '暖暖暂时不在，先看看下面的检测吧'
    }
    return false
  }

  /** 页面挂载后调用：轮询就绪态，就绪即连 WS（只 boot 一次）。
   *  从其他页面回来（单例复用）时：直接恢复常听，会话记忆原样继承。 */
  function init() {
    pageActive = true
    if (disposed) return
    if (booted && connection.value === 'connected') {
      bindUnlock()
      autoListen()
      return
    }
    checkReadiness().then((ok) => {
      if (ok && !disposed) boot()
    })
    readinessTimer = setInterval(async () => {
      const ok = await checkReadiness()
      if (ok && !disposed) boot()
    }, 3000)
  }

  async function boot() {
    if (booted) {
      // 回到首页时可能已被取代/断线（superseded 且 socket 已关）：booted 已置真
      // 但连接死了——不重开的话 boot() 在这里空转，页面永远停在 failed，
      // 表现为"从检测页回到首页后再也连不上"（2026-10-25 实测）。重新参战：
      // 新 socket 代数大于让位代数，configured 守卫会放行并复活本页
      if (!ws || ws.readyState > 1) {
        superseded = false
        micYielded.value = false
        stopReadinessPolling()
        openSocket()
      }
      return
    }
    booted = true
    stopReadinessPolling()
    openSocket()
  }

  // ---------- 对话 WebSocket（唯一通道：文本/语音上行，事件+PCM 下行） ----------
  function openSocket() {
    if (disposed) return
    if (ws && ws.readyState <= 1) return
    connection.value = 'connecting'
    if (booted) connectionDetail.value = '正在连接暖暖…'
    const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
    const url = `${protocol}://${new URL(BASE).host}/ws/conversation`
    ws = new WebSocket(url)
    ws.__gen = ++socketGen
    ws.binaryType = 'arraybuffer'
    ws.addEventListener('open', () => {
      // mode=default：不建 WebRTC、不占 Wav2Lip 渲染；persona=guide 切暖暖
      // 人设与男孩音色；wake_word=你好：常听模式下打招呼进入会话
      ws.send(
        JSON.stringify({
          type: 'configure',
          mode: 'default',
          persona: 'guide',
          wake_word: '你好',
          page_token: PAGE_TOKEN,
        }),
      )
    })
    ws.addEventListener('message', onSocketMessage)
    ws.addEventListener('close', () => {
      ws = null
      flushStreamPlayback()
      speaking.value = false
      busy.value = false
      talking.value = false
      if (listening.value) {
        listening.value = false
        stopMicTracks()
      }
      if (disposed || superseded) return
      // 断线自动重连（引擎重启等）：常听常在，重连后自动重新开麦。
      // superseded（被新连接取代的旧闭包）绝不重连。
      connection.value = 'connecting'
      connectionDetail.value = '暖暖走神了，正在重连…'
      clearTimeout(reconnectTimer)
      reconnectTimer = setTimeout(() => {
        if (!disposed) openSocket()
      }, 2500)
    })
    ws.addEventListener('error', () => {
      if (connection.value !== 'connected') {
        connection.value = 'failed'
        connectionDetail.value = '连接暖暖失败，稍后自动重试'
      }
    })
  }

  function onSocketMessage(message) {
    lastWsActivityAt = Date.now()
    if (typeof message.data === 'string') {
      try {
        handleEvent(JSON.parse(message.data))
      } catch {}
      return
    }
    if (message.data instanceof ArrayBuffer) handleBinary(message.data)
  }

  // ---------- 回复语音：WS 二进制 PCM → AudioContext 顺序排播 ----------
  async function ensureAudioContext() {
    if (!audioCtx || audioCtx.state === 'closed') {
      audioCtx = new AudioContext({ sampleRate: 16000 })
      workletReady = false
      nextStart = 0
    }
    if (audioCtx.state === 'suspended') await audioCtx.resume()
    return audioCtx
  }

  function schedulePcmChunk(pcm) {
    const ctx = audioCtx
    const buffer = ctx.createBuffer(1, pcm.length, 16000)
    const channel = buffer.getChannelData(0)
    for (let i = 0; i < pcm.length; i++) channel[i] = pcm[i] / 32768
    const source = ctx.createBufferSource()
    source.buffer = buffer
    source.connect(ctx.destination)
    const start = Math.max(ctx.currentTime + 0.06, nextStart)
    source.start(start)
    nextStart = start + buffer.duration
    playSources.add(source)
    source.addEventListener('ended', () => playSources.delete(source), { once: true })
  }

  function handleBinary(data) {
    const bytes = new Uint8Array(data)
    if (bytes.length <= 1 || bytes[0] !== 0x01) return
    const pcm = new Int16Array(bytes.slice(1).buffer)
    if (!audioCtx || audioCtx.state !== 'running') {
      // 无手势时输出被自动播放策略压住：先攒着，解锁后按序补播
      pendingPcm.push(pcm)
      if (pendingPcm.length >= 2) needPlayTap.value = true
      return
    }
    schedulePcmChunk(pcm)
  }

  function flushPendingPcm() {
    if (!audioCtx || audioCtx.state !== 'running' || !pendingPcm.length) return
    const chunks = pendingPcm
    pendingPcm = []
    for (const chunk of chunks) schedulePcmChunk(chunk)
  }

  /** 立即清空本地播放（打断/新话轮/断线）：停掉在播与排队，指针归零。 */
  function flushStreamPlayback() {
    clearTimeout(drainTimer)
    drainToken++
    playSources.forEach((source) => {
      try {
        source.stop()
      } catch {}
    })
    playSources.clear()
    pendingPcm = []
    if (audioCtx) nextStart = audioCtx.currentTime
    needPlayTap.value = false
  }

  /** response_done 后等本地队列播完再收口（估剩余时长 + 尾巴）。 */
  function finishReplyWhenDrained() {
    clearTimeout(drainTimer)
    const token = ++drainToken
    const remaining =
      audioCtx && audioCtx.state === 'running'
        ? Math.max(0, nextStart - audioCtx.currentTime)
        : 0
    drainTimer = setTimeout(() => {
      if (token !== drainToken) return
      speaking.value = false
      busy.value = false
      talking.value = false
      connectionDetail.value = '说完啦，继续聊或选择模块'
    }, remaining * 1000 + 150)
  }

  // ---------- 事件 ----------
  function handleEvent(event) {
    switch (event.type) {
      case 'configured': {
        // 让位守卫：本页已让出（mic_taken）后，只有让位之后**自己新建**的
        // socket（用户点"刷新本页接管麦克风"的 retry 路径）上的 configured
        // 才算数；让位那把老 socket 上跟来的 configured 是引擎拒绝无令牌
        // 连接时的成对回执，若照常清 micYielded 并 autoListen，本页会立即
        // 重新 getUserMedia 抢走物理麦克风——新任页面从此永远开不了麦
        if (
          (micYielded.value || superseded) &&
          !(ws && ws.__gen > yieldedAtSocketGen)
        ) {
          connectionDetail.value = '本窗口已让出麦克风，保持静音；刷新本页可接管'
          break
        }
        // 同页透明重连（引擎重启/断线自愈）时唤醒状态跟着会话保留：
        // configure 后立刻补一声 awake，免得用户每经历一次引擎重启就被
        // 重新武装的唤醒门拦住（实测"只有你好触发 TTS"的根因之一）。
        // 手动刷新整页仍重新武装——符合「你好＝新访客」语义。
        const wasAwake = awake.value
        connection.value = 'connected'
        // 新连接成为麦克风持有者（引擎仲裁：最新 configure 接管）
        micYielded.value = false
        superseded = false
        awake.value = false // 唤醒门重新武装，听到「你好」才亮面板
        if (wasAwake && ws && ws.readyState === 1) {
          ws.send(JSON.stringify({ type: 'awake' }))
          awake.value = true
        }
        connectionDetail.value = '聆听中，说「你好」开始对话'
        bindUnlock()
        autoListen()
        break
      }
      case 'mic_taken':
        // 引擎仲裁：更新的连接接管了（HMR 旧闭包/另一个窗口）。本闭包彻底
        // 退场：关 socket、标记 superseded（close 处理器见之不再重连）——
        // 否则旧闭包的断线重连会不断新建连接把新连接挤下线，可见页面
        // 从此收不到任何事件（"你好没用/跳转失败"的根因）。
        micYielded.value = true
        yieldedAtSocketGen = ws ? ws.__gen || 0 : socketGen
        listening.value = false
        stopMicTracks()
        superseded = true
        try {
          ws?.close()
        } catch {}
        // 必须降级连接状态：否则输入框仍按 connected 保持可用，
        // 打字发进已关闭的 socket 里静默失败（实测"打字跳转没反应"的坑）
        connection.value = 'failed'
        connectionDetail.value = '麦克风已在另一窗口打开，本窗口静音；刷新本页切换过来'
        // 自毁只在本页正在展示首页时执行（pageActive）。SPA 离开首页后单例
        // WS 仍保留，此时被抢麦若照常 location.replace，会把用户正在看的
        // 检测页硬劫到 superseded 占位页——只静音退场，回来时 init 重连接管
        if (pageActive) {
          try {
            window.close()
          } catch {}
          setTimeout(() => {
            location.replace('/superseded.html')
          }, 400)
        }
        break
      case 'hint':
        // 话轮以此收尾（没听清/回声等）＝本轮结束：必须清 busy，否则它会
        // 残留成"回复还在进行"的假象——之后每次开口都被当成抢话打断，
        // cancel 让引擎丢弃正在说的话，检测彻底死掉（实测"未唤醒说一句
        // 之后就不再识别"的根因）
        busy.value = false
        // 环境音/短噪声的静默收口：不计入"没听清"（防误触发采音重启）、
        // 不进面板、不改状态行——噪声话轮用户无需感知
        if (event.quiet) break
        // 注意：绝不清 talking——"没听清"属于用户话轮，与正在播放的回复无关；
        // 曾在此清 talking 导致播报中一条回声空转写提示就把说话动画硬切回
        // 待机（呼吸），而语音继续播＝"嘴停了还在说"（2026-10-25 用户实锤）。
        // talking 只由 audio_start/response_done 播空与 interrupt 管理。
        // 连续 3 次"没听清"（有能量但转不出内容）＝采音链路疑似坏死，
        // 自动重启一次麦克风；识别成功即清零
        emptyStreak += 1
        if (awake.value && emptyStreak >= 3) restartMic('连续没听清 x3')
        if (event.message) {
          connectionDetail.value = event.message
          pushHeard('提示', event.message)
        }
        break
      case 'awake':
        awake.value = true
        connectionDetail.value = '麦克风已开启，直接说就好'
        break
      case 'wake_word_pending':
        // 同上：被唤醒门拦下的话轮也是完整结束的一轮，busy 必须清；
        // talking 不清（理由见 hint 分支）
        busy.value = false
        lastHeard.value = `（未唤醒）${event.text || ''}`
        pushHeard('未唤醒', lastHeard.value)
        connectionDetail.value = `没听懂整句，说「${event.wakeWord || '你好'}」再开始`
        break
      case 'wake':
        awake.value = true
        connectionDetail.value = '已唤醒，请直接说'
        break
      case 'emotion':
        emotion.value = {
          key: event.emotion,
          label: event.label,
          changed: !!event.changed,
          consecutive: event.consecutive || 1,
          hint: event.hint || '',
        }
        break
      case 'doc':
        // 只在教学/说明性回复（LLM 显式 [DOC:]）到达
        docCard.value = { title: event.title || '重要资料', points: event.points || [] }
        break
      case 'recommend': {
        // 本页只承接模块推荐；跳舞邀请（kind=dance）属于陪伴页，忽略
        if (event.kind !== 'module') break
        // 注意带上 kind:'module'——页面侧推荐卡 computed 靠它识别
        recommendation.value = {
          kind: 'module',
          key: event.module || event.emotion || '',
          reason: event.reason || '',
          emotion: event.emotion || '',
        }
        if (event.reason) connectionDetail.value = `已推荐检测模块：${event.reason}`
        break
      }
      case 'speech_started':
        // 抢话打断只在数字人**正在出声**时发 cancel（打断的是在播回复）。
        // 绝不能在 busy（话轮处理中）时打断：cancel 会作废引擎里正在跑的
        // 这个话轮，转写被静默丢弃、页面 busy 永久卡死，且每个新
        // speech_started 都重演一次＝自毁循环（2026-10-25 实测"又卡住"）
        if (speaking.value) interrupt('user')
        busy.value = true
        speaking.value = false
        // 用户开口说话：旧回复正在被打断，说话动画先收（新 transcript 会再起）
        talking.value = false
        connectionDetail.value = '正在听你说话…'
        break
      case 'speech_stopped':
        connectionDetail.value = '正在识别你的语音…'
        break
      case 'pipeline':
        if (event.stage === 'llm') connectionDetail.value = '正在思考…'
        break
      case 'transcript':
        emptyStreak = 0
        lastHeard.value = event.text || ''
        pushHeard('识别', lastHeard.value)
        busy.value = true
        // 新话轮受理：停掉本地残留音频（服务端已在 transcript 前取消旧回复），
        // 立刻驱动动画数字人进说话链（语音还要约 2 秒才出声，正好走过渡段）
        flushStreamPlayback()
        talking.value = true
        connectionDetail.value = '正在思考…'
        break
      case 'assistant_delta':
        lastReply.value += event.text || ''
        break
      case 'audio_start':
        // 服务端首块音频已发出：语音马上到（本地排队播放）
        busy.value = false
        speaking.value = true
        connectionDetail.value = '暖暖正在说话…'
        break
      case 'response_done':
        // 文本与音频都已发完：等本地队列播空再收口
        lastReply.value = ''
        finishReplyWhenDrained()
        break
      case 'response_accepted':
        lastReply.value = ''
        break
      case 'tts_fallback':
        connectionDetail.value = '（本段使用备用语音）'
        break
      case 'session_reset':
        // 刷新口令/裸「你好」＝新会话：识别面板必须清空重来（用户拍板
        // 2026-10-26"说你好后会话框要刷新"），只留一条系统提示标记起点。
        // 系统提示必须先入日志再武装唤醒门：门归 false 后 pushHeard 全被拦，
        // 这条提示永远显示不出来；软重置（你好）路径随后还有 wake 事件把门
        // 置回 true，顺序与引擎一致
        heardLog.value = []
        pushHeard('系统', '会话已重新开始')
        awake.value = false
        sessionReset.value += 1
        lastReply.value = ''
        lastHeard.value = ''
        flushStreamPlayback()
        talking.value = false
        busy.value = false
        speaking.value = false
        connectionDetail.value = '会话已重新开始，说吧'
        break
      case 'module_jump':
        // 用户语音确认了模块推荐 → 页面跳转对应检测
        moduleJump.value = { module: event.module || '', reason: event.reason || '' }
        break
      case 'error':
        flushStreamPlayback()
        busy.value = false
        speaking.value = false
        talking.value = false
        connectionDetail.value = event.message || '处理失败'
        break
    }
  }

  // ---------- 交互 ----------
  function sendText(text) {
    const content = (text || '').trim()
    if (!content || connection.value !== 'connected') return false
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify({ type: 'text', text: content }))
      busy.value = true
      talking.value = true
      connectionDetail.value = '正在思考…'
      return true
    }
    return false
  }

  function interrupt(source = 'user') {
    const wasSpeaking = speaking.value || busy.value
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify({ type: 'cancel' }))
    }
    flushStreamPlayback()
    busy.value = false
    speaking.value = false
    talking.value = false
    if (source === 'button' && wasSpeaking) connectionDetail.value = '已打断，我在听'
  }

  async function toggleMic() {
    if (listening.value) {
      if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'cancel' }))
      listening.value = false
      stopMicTracks()
      connectionDetail.value = '麦克风已关闭'
      return
    }
    // 并行开麦进行中（如页面点击触发的自动补开）：等它结束再执行按钮意图，
    // 否则按钮这次开麦被 micBusy 吞掉=点了没反应（用户实测"关了打不开"）
    for (let i = 0; i < 20 && micBusy; i++) {
      await new Promise((r) => setTimeout(r, 100))
    }
    await enableMic({ awake: true })
  }

  /** 等 WS 打开（openSocket 不等待 open 事件，直接开麦会丢 PCM=开而聋）。 */
  function waitSocketOpen(timeoutMs = 3000) {
    return new Promise((resolve) => {
      const t0 = Date.now()
      const iv = setInterval(() => {
        if ((ws && ws.readyState === 1) || ws === null || Date.now() - t0 > timeoutMs) {
          clearInterval(iv)
          resolve()
        }
      }, 80)
    })
  }

  /** 开麦。awake=true（手动点按钮）跳过唤醒词；false（常听）由「暖暖」唤醒。 */
  async function enableMic({ awake = true } = {}) {
    // 已在听就不能再开：并发路径（按钮开麦+configured 触发的自动补开）下二次
    // getUserMedia 会把旧 micStream 覆盖掉且不停轨道，设备在本页内被永久占住
    if (micBusy || listening.value) return
    micBusy = true
    try {
      if (!ws || ws.readyState !== 1) {
        openSocket()
        await waitSocketOpen()
      }
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
      // 诊断分析器：设备名 + 实时输入音量（右栏识别面板显示）
      try {
        const track = micStream.getAudioTracks()[0]
        micLabel.value = track ? track.label : ''
        micAnalyser = context.createAnalyser()
        micAnalyser.fftSize = 512
        micLevelBuf = new Float32Array(micAnalyser.fftSize)
        micSource.connect(micAnalyser)
      } catch {}
      micNode = new AudioWorkletNode(context, 'home-guide-mic')
      micNode.port.onmessage = (message) => {
        lastPcmAt = Date.now()
        if (ws && ws.readyState === 1) ws.send(message.data)
      }
      micSource.connect(micNode)
      // 零增益下沉：拉活 Worklet 且不外放麦克风声音（防回声）
      micSink = context.createGain()
      micSink.gain.value = 0
      micNode.connect(micSink)
      micSink.connect(context.destination)
      listening.value = true
      // 挂起自愈：页面后台时 Chrome 会挂起 AudioContext（worklet 停转→PCM 断流
      // →引擎话轮悬死）。每 3s 检查并 resume，不等用户回页面点击。
      if (!ctxKeepAliveTimer) {
        ctxKeepAliveTimer = setInterval(async () => {
          if (disposed || !listening.value) {
            micLevel.value = 0
            return
          }
          if (audioCtx && audioCtx.state === 'suspended') {
            try { await audioCtx.resume() } catch {}
          }
          if (micAnalyser) {
            micAnalyser.getFloatTimeDomainData(micLevelBuf)
            let sum = 0
            for (let i = 0; i < micLevelBuf.length; i++) sum += micLevelBuf[i] * micLevelBuf[i]
            micLevel.value = Math.min(1, Math.sqrt(sum / micLevelBuf.length) * 12)
          }
          // 采音看门狗：上下文在跑、麦开着，但 worklet 2 秒没出 PCM = 采音坏死
          if (listening.value && !superseded && lastPcmAt && Date.now() - lastPcmAt > 2000) {
            lastPcmAt = Date.now() // 防止重启期间重复触发
            restartMic('PCM 断流 2s')
          }
        }, 150)
      }
      // 手动点麦克风是明确对话意图：开闸免唤醒词；常听模式走唤醒词门控
      if (awake && ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'awake' }))
      if (context.state !== 'running') {
        // 浏览器自动播放策略：无手势时上下文被压住，收不到音——
        // 提示点一下页面（bindUnlock 的监听会 resume 并补开麦）
        connectionDetail.value = '点一下页面任意处，暖暖开始收音'
      } else {
        connectionDetail.value = awake ? '麦克风已开启，直接说吧' : '聆听中，说「你好」开始对话'
      }
    } catch (err) {
      connectionDetail.value = `麦克风开启失败：${err.message || err}`
      // 中途失败（getUserMedia 成功但后续步骤炸了）必须回收半开的流：泄漏的
      // 轨道会在本页内一直占住设备，后续每次重试都 NotReadableError
      if (micStream && !listening.value) stopMicTracks()
    } finally {
      micBusy = false
    }
  }

  /**
   * 常听：连接后自动开麦（唤醒词门控）。首次任意点击/按键/切回标签页时：
   * 恢复被自动播放策略压住的 AudioContext、补播被扣住的回复音频、
   * 麦克风没开成则重试——常听常在，不因策略卡死。
   */
  async function autoListen() {
    if (listening.value || micYielded.value || superseded || !pageActive || connection.value !== 'connected') return
    // 单飞：重连/configured 会多次触发 autoListen，叠出的并发重试循环会互相
    // 抢 micBusy 空转并重复 getUserMedia；已有循环在跑就直接返回
    if (autoListenActive) return
    autoListenActive = true
    try {
      bindUnlock()
      // 开麦**无限重试直到成功**（2026-10-25 用户拍板"不停地请求，直到开启"）：
      // 刷新瞬间旧页面的麦克风流没释放完，新页面 getUserMedia 会被拒
      // （NotReadableError 竞态，释放窗口时长不定，固定 3 次不够）。退避重试，
      // 每次失败把原因+次数写进状态行；一旦页面被取代/离开/断线立即停手。
      let lastErr = ''
      for (let attempt = 1; ; attempt++) {
        await enableMic({ awake: false })
        if (listening.value) return
        if (micYielded.value || superseded || !pageActive || disposed || connection.value !== 'connected') return
        lastErr = (connectionDetail.value.match(/麦克风开启失败：(.+)/) || [])[1] || lastErr || '设备未就绪'
        const wait = Math.min(1000 + attempt * 500, 4000)
        const permHint = /NotAllowed|权限|Permission/i.test(lastErr) ? '；请在浏览器地址栏允许麦克风权限' : ''
        connectionDetail.value = `麦克风开启失败（${lastErr}${permHint}），${Math.round(wait / 1000)}秒后第 ${attempt + 1} 次尝试…`
        await new Promise((r) => setTimeout(r, wait))
      }
    } finally {
      autoListenActive = false
    }
  }

  function bindUnlock() {
    if (unlockBound) return
    unlockBound = true
    const unlock = async (ev) => {
      if (disposed) return
      // 恢复音频上下文 + 补播扣住的音频对任何点击都生效（含控制台内部——
      // 此前控制台点击被整体跳过，用户对着输入框点永远解不了锁，音量条恒 0）
      let ctx = null
      try {
        ctx = await ensureAudioContext()
      } catch {
        return
      }
      if (!ctx || ctx.state !== 'running') return
      flushPendingPcm()
      if (needPlayTap.value) needPlayTap.value = false
      // 点击本来就在对话控制台里（麦克风/发送等）：交给按钮自己的处理器，
      // 自动补开在此抢跑会用 micBusy 把按钮的开麦吞掉
      if (ev && ev.target && ev.target.closest && ev.target.closest('.core-console')) return
      // 重连后/解锁后麦没开成：补开（常听）；仲裁让出或已离开首页则不抢开
      if (
        !listening.value &&
        !micStream &&
        !micYielded.value &&
        pageActive &&
        connection.value === 'connected'
      ) {
        await enableMic({ awake: false })
      }
    }
    document.addEventListener('pointerdown', unlock)
    document.addEventListener('keydown', unlock)
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) unlock()
    })
  }

  /** 点击解锁输出（被自动播放策略扣住的回复音频补播）。 */
  function tapPlay() {
    ensureAudioContext()
      .then((ctx) => {
        if (ctx.state === 'running') {
          needPlayTap.value = false
          flushPendingPcm()
        }
      })
      .catch(() => {})
  }

  /** 自动重启采音（2026-10-25 自愈）：浏览器采音链路偶发坏死——
   *  上下文在跑、音量条在动，但 worklet 出来的 PCM 是坏帧（引擎转写
   *  出'嘘。'这类碎片或全空）。停掉轨道重开一次通常即愈。 */
  async function restartMic(reason) {
    if (disposed || micYielded.value || superseded || micRestarts >= 5) return
    micRestarts += 1
    emptyStreak = 0
    try {
      console.warn(`[HomeGuide] 重启麦克风（${reason}，第 ${micRestarts} 次）`)
      if (listening.value) {
        listening.value = false
        stopMicTracks()
      }
      await enableMic({ awake: false })
    } catch {}
  }

  function stopMicTracks() {
    micAnalyser = null
    micLevelBuf = null
    micLevel.value = 0
    if (ctxKeepAliveTimer) {
      clearInterval(ctxKeepAliveTimer)
      ctxKeepAliveTimer = 0
    }
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

  /** 连接失败后手动重试。 */
  async function retry() {
    try {
      ws?.close()
    } catch {}
    ws = null
    booted = true
    openSocket()
  }

  /** 预置播报（不调 LLM、不写记忆）：检测结果回读等一次性讲解用。 */
  function speak(text) {
    const content = (text || '').trim()
    if (!content || connection.value !== 'connected') return false
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify({ type: 'speak', text: content }))
      busy.value = true
      talking.value = true
      return true
    }
    return false
  }

  function clearance() {
    recommendation.value = null
  }

  return {
    // 状态
    connection,
    connectionDetail,
    serviceReady,
    listening,
    busy,
    speaking,
    talking,
    needPlayTap,
    emotion,
    recommendation,
    docCard,
    lastReply,
    lastHeard,
    awake,
    // 静音横幅用（曾漏导出导致首页白屏：模板读 talk.micYielded.value →
    // undefined.value 崩掉整个渲染。教训：模板新增字段先查导出列表）
    micYielded,
    heardLog,
    micLabel,
    micLevel,
    moduleJump,
    sessionReset,
    // 方法
    init,
    autoListen,
    // 必须导出：useHomeGuide 的 onBeforeUnmount 钩子调用它停麦。曾漏导出，
    // 钩子里 TypeError 炸断 Vue 卸载链 → 路由跳转后旧视图钉死在新 URL 上
    // （首页点检测按钮/语音跳转"卡住"的根因），且离开首页后麦克风从未停过
    suspendForNavigation,
    tapPlay,
    sendText,
    interrupt,
    retry,
    clearance,
    speak,
  }
}
