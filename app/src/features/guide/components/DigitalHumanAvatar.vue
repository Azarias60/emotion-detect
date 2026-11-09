<template>
  <div class="dh-avatar" :class="`dh-${size}`">
    <div class="dh-stage">
      <video
        ref="videoA"
        class="dh-video"
        :class="{ 'dh-active': activeLayer === 'a' }"
        playsinline
        webkit-playsinline
        preload="auto"
        muted
        @ended="onEnded('a')"
      ></video>
      <video
        ref="videoB"
        class="dh-video"
        :class="{ 'dh-active': activeLayer === 'b' }"
        playsinline
        webkit-playsinline
        preload="auto"
        muted
        @ended="onEnded('b')"
      ></video>
      <!-- 口型同步画布：盖在视频嘴部位置，按语音词边界选嘴形帧 -->
      <canvas
        ref="mouthCanvas"
        class="dh-mouth"
        :class="{ 'dh-mouth-on': mouthOn }"
        width="184"
        height="114"
      ></canvas>
    </div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'

/**
 * 3D 数字人视频状态机（逻辑移植自 LiveTalking state_machine.py）。
 *
 * 素材：public/digital-human/ 下 5 个段（idle1/2/3、greet、talk）
 * + 20 个过渡（T_段A2段B.mp4），全部 704x1280 黑底、20fps。
 * 黑底压成纯黑后配 mix-blend-mode:screen 融入全息舞台。
 *
 * 链式规则（对应 LiveTalking 的 on_cycle_complete）：
 * - 待机：循环播随机 idle，段末用 idle↔idle 过渡切换
 * - 首次说话：idle →(idle2greet)→ greet →(greet2talk)→ talk
 * - 再次说话：idle →(idle2talk)→ talk
 * - 说话中 talk 播完：talk2talk → talk 循环，直到说完
 * - 说完：talk →(talk2idleN)→ 随机 idle
 *
 * 双 video 层缓冲（关键，防黑屏）：
 * 当前段播在激活层；下一段始终预载到后台层。切换时只激活已就绪的后台层，
 * 可见层的 src 永远不被替换——任何情况下都不会出现加载黑帧。
 * 后台层未就绪时可见层停在末帧等待，就绪后再切。
 */

const BASE = '/digital-human'
const IDLES = ['idle1', 'idle2', 'idle3']
const SEG_FILE = (name) => `${BASE}/${name}.mp4`
const TRANS_FILE = (a, b) => `${BASE}/T_${a}2${b}.mp4`

const props = defineProps({
  /** idle=只待机（首页用）；assistant=可被驱动说话（陪伴模块用） */
  mode: { type: String, default: 'idle' },
  /** sm=首页中央人物；lg=陪伴模块大画面 */
  size: { type: String, default: 'sm' },
})

const emit = defineEmits(['state-change'])

const videoA = ref(null)
const videoB = ref(null)
const activeLayer = ref('a')

// ---- 口型同步（按语音词边界驱动）----
const mouthCanvas = ref(null)
const mouthOn = ref(false)
let mouthCrops = [] // Image[]：talk 段 49 个嘴形帧（带羽化 Alpha）
let mouthCurve = [] // number[]：每帧开口度 0-1
let mouthLoaded = false
let mouthOpenness = -1 // -1 = 不驱动（露视频自己的嘴）

/** 后台预载嘴形资产（1.8MB，本地加载一次）。 */
function loadMouthAssets() {
  if (mouthLoaded) return
  mouthLoaded = true // 防重复
  const jobs = []
  for (let i = 0; i < 49; i++) {
    const idx = String(i).padStart(2, '0')
    jobs.push(
      new Promise((res) => {
        const im = new Image()
        im.onload = () => res(im)
        im.onerror = () => res(null)
        im.src = `/digital-human/talk_mouth/f${idx}.png`
      }),
    )
  }
  Promise.all([
    fetch('/digital-human/talk_mouth/curve.json').then((r) => r.json()),
    ...jobs,
  ]).then(([curve, ...imgs]) => {
    mouthCurve = curve
    mouthCrops = imgs
  })
}

/** 设置当前嘴部开口度（0-1）；传 -1 停止驱动。 */
function setMouthOpenness(v) {
  if (v === mouthOpenness) return
  mouthOpenness = v
  mouthOn.value = v >= 0
  drawMouth()
}

function drawMouth() {
  const c = mouthCanvas.value
  if (!c || mouthOpenness < 0 || !mouthCurve.length) return
  const ctx = c.getContext('2d')
  // 选开口度最接近的嘴形帧
  let best = 0
  let bd = 1e9
  for (let i = 0; i < mouthCurve.length; i++) {
    const d = Math.abs(mouthCurve[i] - mouthOpenness)
    if (d < bd) {
      bd = d
      best = i
    }
  }
  const img = mouthCrops[best]
  if (!img) return
  ctx.clearRect(0, 0, c.width, c.height)
  ctx.drawImage(img, 0, 0, c.width, c.height)
}

// ---- 状态机状态 ----
let current = 'idle1'
let inTransition = false
let transitionTarget = null
let speaking = false
let mustReachTalk = false // 本次说话必须走到 talk 段（语音短也不能在 greet 半路折回 idle）
let firstUtteranceDone = false
let pendingTalk = false // 过渡中被要求说话（非首次）
let pendingGreet = false // 过渡中被要求首次说话（要走 greet 链）
let destroyed = false
let retryPlayBound = false
// 每层的 src 占用标记：playNext 装入期间防止 preload 抢掉（曾导致播放错位一个片段）
const layerCommit = { a: null, b: null }

function setState(name) {
  current = name
  emit('state-change', name)
  console.log(`[DH ${new Date().toLocaleTimeString()}] 动作 -> ${name}`)
}

function pickIdle(exclude) {
  const choices = IDLES.filter((n) => n !== exclude)
  return choices[Math.floor(Math.random() * choices.length)] || IDLES[0]
}

function other(layer) {
  return layer === 'a' ? 'b' : 'a'
}

function el(layer) {
  return layer === 'a' ? videoA.value : videoB.value
}

function clipName(src) {
  return (src || '').split('/').pop()
}

function bindRetryPlay() {
  if (retryPlayBound) return
  retryPlayBound = true
  const retry = () => {
    const v = el(activeLayer.value)
    if (v && v.paused && !v.ended) {
      const p = v.play()
      if (p && p.catch) p.catch(() => {})
    }
  }
  document.addEventListener('pointerdown', retry, { once: true })
  document.addEventListener('keydown', retry, { once: true })
  // 切回标签页时恢复播放（浏览器会在后台标签页暂停视频）
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) retry()
  })
}

/** 把某层装入指定片段（已是目标则不动）。 */
function setLayerSrc(layer, src) {
  const v = el(layer)
  if (!v) return
  if (v.src && v.src.endsWith(src)) return
  v.src = src
  v.load()
}

/** 预载下一段到后台层（不抢 playNext 正在装入的层）。 */
function preload(src) {
  const layer = other(activeLayer.value)
  if (layerCommit[layer]) return
  setLayerSrc(layer, src)
}

/** 激活某一层（该层必须已就绪）并播放。 */
function switchTo(layer, src) {
  const v = el(layer)
  if (!v) return
  layerCommit[layer] = null
  console.log(`[DH ${new Date().toLocaleTimeString()}] switchTo ${clipName(v.src || src)} @层${layer} rs=${v.readyState}`)
  activeLayer.value = layer
  v.currentTime = 0
  const p = v.play()
  if (p && p.catch) p.catch(() => bindRetryPlay())
}

/**
 * 播放下一个片段：优先激活已预载好的后台层（零等待、不黑屏）；
 * 后台层没就绪就等它 loadeddata 再接上（可见层停在末帧，不出黑帧）。
 * 装入期间给该层打占用标记，防止随后的 preload 把 src 抢掉。
 */
function playNext(src) {
  console.log(`[DH ${new Date().toLocaleTimeString()}] playNext 请求 ${clipName(src)}（当前层${activeLayer.value}）`)
  const back = other(activeLayer.value)
  const vBack = el(back)
  if (!vBack) return
  layerCommit[back] = src
  if (vBack.src && vBack.src.endsWith(src) && vBack.readyState >= 3) {
    layerCommit[back] = null
    switchTo(back, src)
    return
  }
  const onReady = () => {
    layerCommit[back] = null
    if (!destroyed) switchTo(back, src)
  }
  vBack.addEventListener('loadeddata', onReady, { once: true })
  setLayerSrc(back, src)
}

function activate(name) {
  console.log(`[DH ${new Date().toLocaleTimeString()}] activate(${name})`)
  if (name === 'talk') mustReachTalk = false
  inTransition = false
  transitionTarget = null
  setState(name)
  playNext(SEG_FILE(name))
  const next = nextAfter(name)
  if (next) preload(next)
}

/** 当前段播完后下一步播什么（供预载）。 */
function nextAfter(name) {
  if (name === 'talk') {
    return speaking ? TRANS_FILE('talk', 'talk') : TRANS_FILE('talk', pickIdle())
  }
  if (IDLES.includes(name)) {
    return speaking ? TRANS_FILE(name, 'talk') : TRANS_FILE(name, pickIdle(name))
  }
  if (name === 'greet') {
    return TRANS_FILE('greet', 'talk')
  }
  return null
}

function triggerTransition(from, to) {
  console.log(`[DH ${new Date().toLocaleTimeString()}] triggerTransition ${from} -> ${to}`)
  if (to === 'talk') mustReachTalk = false
  if (from === to) {
    if (from === 'talk') {
      // talk→talk 循环必须走 T_talk2talk 过渡片：talk 段首尾姿势不同，
      // 直接重播是硬跳变（用户实测"一直重复一个视频没过渡段"）。
      // 素材接缝按 talk→T_talk2talk→talk 设计（端点帧差 3.2/3.9 实测合格），
      // 且 nextAfter 已把过渡片预载到后台层，切换零等待。
      inTransition = true
      transitionTarget = 'talk'
      emit('state-change', 'talk→talk')
      playNext(TRANS_FILE('talk', 'talk'))
      preload(SEG_FILE('talk'))
      return
    }
    activate(to)
    return
  }
  inTransition = true
  transitionTarget = to
  emit('state-change', `${from}→${to}`)
  playNext(TRANS_FILE(from, to))
  preload(SEG_FILE(to))
}

// 呼吸感定格：idle 段末偶尔停 1~2 秒的概率
const HOLD_CHANCE = 0.35
let holdTimer = null

/** 从 idle 段出发的路由（onEnded 与定格计时器共用）。 */
function routeFromIdle() {
  const state = current
  if (speaking || mustReachTalk) {
    mustReachTalk = false
    if (wantGreet) {
      wantGreet = false
      triggerTransition(state, 'greet')
    } else {
      triggerTransition(state, 'talk')
    }
  } else {
    triggerTransition(state, pickIdle(state))
  }
}

function onEnded(layer) {
  console.log(`[DH ${new Date().toLocaleTimeString()}] onEnded 层${layer}（激活层${activeLayer.value}，状态${current}，过渡中=${inTransition}）`)
  if (layer !== activeLayer.value) return

  if (inTransition) {
    const target = transitionTarget
    activate(target)
    if (pendingGreet) {
      pendingGreet = false
      pendingTalk = false
      if (target !== 'greet') triggerTransition(target, 'greet')
    } else if (pendingTalk && IDLES.includes(target)) {
      pendingTalk = false
      triggerTransition(target, 'talk')
    }
    return
  }

  const state = current
  if (IDLES.includes(state)) {
    // 呼吸感（用户拍板 2026-10-23）：idle 段播完后偶尔定格在最后一帧
    // 1~2 秒再动——不是一直小幅乱动，偶尔安静一下更自然。说话/打断会
    // 立即取消定格进入说话链，不影响响应。
    if (!holdTimer && Math.random() < HOLD_CHANCE) {
      const ms = 1000 + Math.random() * 1000
      holdTimer = setTimeout(() => {
        holdTimer = null
        if (destroyed) return
        if (current === state && !inTransition) routeFromIdle()
      }, ms)
      return
    }
    routeFromIdle()
    return
  }
  if (state === 'greet') {
    // 首次说话必须走完 greet→talk，哪怕语音已经说完
    if (speaking || mustReachTalk) {
      mustReachTalk = false
      triggerTransition('greet', 'talk')
    } else {
      triggerTransition('greet', pickIdle())
    }
    return
  }
  if (state === 'talk') {
    if (speaking) {
      triggerTransition('talk', 'talk')
    } else {
      mustReachTalk = false
      triggerTransition('talk', pickIdle())
    }
  }
}

// ---- 对外接口 ----

// 首次说话想走 greet 链：只在段末路由时消费（startSpeaking 不再立即切）
let wantGreet = false

/** 开始说话：只立旗标，不立即切。
 *  立即切会从当前段中间硬切到过渡片第 0 帧（姿势不匹配=闪切，实测
 *  idle2@0.62s 被切到 T_idle22greet 闪屏）；改为等当前段自然播完，
 *  onEnded 按旗标从端点出发，接缝由素材保证无缝。语音不受影响。 */
function startSpeaking({ first = false } = {}) {
  speaking = true
  mustReachTalk = true
  if (first && !firstUtteranceDone) {
    firstUtteranceDone = true
    wantGreet = true
  }
  // 定格中开口说话：立即取消定格并路由进说话链
  if (holdTimer) {
    clearTimeout(holdTimer)
    holdTimer = null
    if (!inTransition && IDLES.includes(current)) routeFromIdle()
  }
}

/** 说完：只清旗标。talk/greet 段自然播完后 onEnded 会过渡回 idle——
 *  立即切同样会从段中间硬切（闪切实测点之二）。 */
function stopSpeaking() {
  speaking = false
}

/** 首句语音起播时立即硬切到 greet（用户拍板 2026-10-23：第一句不等过渡、
 *  greet 与语音同时开始）。从任意状态直接 activate('greet')，接缝的姿势
 *  跳变按用户要求接受；greet 播完后照常走 greet2talk→talk。 */
function greetNow() {
  speaking = true
  mustReachTalk = true
  firstUtteranceDone = true
  wantGreet = false
  if (holdTimer) {
    clearTimeout(holdTimer)
    holdTimer = null
  }
  if (current === 'greet' || current === 'talk') return
  activate('greet')
}

/** 语音起播瞬间硬切进说话链（2026-10-24 用户拍板"张嘴和语音一起播放"）：
 *  idle2talk/talk2talk/greet2talk 都是说话动画，从当前待机/过渡直接硬切
 *  idle2talk，嘴与声音同帧开始，不等当前 idle 段播完（姿势跳变按 greetNow
 *  同样口径接受）。已在 talk 循环（连续第二句回复）则无缝继续。 */
function talkNow() {
  speaking = true
  mustReachTalk = true
  firstUtteranceDone = true
  wantGreet = false
  if (holdTimer) {
    clearTimeout(holdTimer)
    holdTimer = null
  }
  if (current === 'talk') return
  if (inTransition && transitionTarget === 'talk') return
  triggerTransition(current, 'talk')
}

/** 声音播完瞬间硬切出说话链（嘴随声音同帧停，不留"张嘴没声音"尾巴）：
 *  talk/greet 段中直接切回 idle 的过渡片；还在过渡片中（话很短）就直接
 *  落回待机段。 */
function stopTalkingNow() {
  speaking = false
  mustReachTalk = false
  wantGreet = false
  if (holdTimer) {
    clearTimeout(holdTimer)
    holdTimer = null
  }
  if (inTransition) {
    activate(pickIdle())
    return
  }
  if (current === 'talk' || current === 'greet') {
    triggerTransition(current, pickIdle())
  }
}

onMounted(() => {
  setState(current)
  loadMouthAssets()
  // 首帧：装入 A 层并播放
  const v = videoA.value
  if (v) {
    console.log(`[DH ${new Date().toLocaleTimeString()}] 播放 idle1.mp4 @层a`)
    v.addEventListener(
      'loadeddata',
      () => {
        if (destroyed) return
        activeLayer.value = 'a'
        v.currentTime = 0
        // 恢复监听无条件挂载：只挂在 play().catch 里会漏掉"播放成功后被浏览器
        // 暂停"的情况（后台标签页/切窗口时画面会冻住），挂上后点页面/按键/切回标签页都能恢复
        bindRetryPlay()
        const p = v.play()
        if (p && p.catch) p.catch(() => {})
      },
      { once: true },
    )
    v.src = SEG_FILE(current)
    v.load()
  }
})

onBeforeUnmount(() => {
  destroyed = true
  if (holdTimer) clearTimeout(holdTimer)
})

defineExpose({ startSpeaking, stopSpeaking, greetNow, talkNow, stopTalkingNow, currentState: () => current, setMouthOpenness })
</script>

<style scoped>
.dh-avatar {
  position: relative;
  width: 100%;
  height: 100%;
}
/* 全息舞台：舞台底用纯黑——视频背景也是纯黑（0.0），正常混合下完全隐形；
   人物按原色渲染（screen 混合会把暗部提亮、染上舞台蓝调，导致色调失真）。
   边框/辉光/投影光束都是覆盖在视频上层的叠加元素，让画面看起来是刻意的全息投影台。 */
.dh-stage {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
  border-radius: 16px;
  background: #000;
}
/* 顶部投影光束（覆盖层） */
.dh-stage::before {
  content: '';
  position: absolute;
  left: 12%;
  right: 12%;
  top: 0;
  height: 34%;
  background: linear-gradient(to bottom, rgba(0, 190, 255, 0.14), transparent 100%);
  pointer-events: none;
  z-index: 2;
}
/* 脚下辉光 + 全息边框（覆盖层） */
.dh-stage::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 16px;
  border: 2px solid rgba(0, 242, 255, 0.62);
  box-shadow:
    0 0 26px rgba(0, 180, 255, 0.28),
    inset 0 0 40px rgba(0, 120, 180, 0.1);
  background:
    radial-gradient(ellipse 46% 13% at 50% 93%, rgba(0, 200, 255, 0.4), transparent 70%);
  pointer-events: none;
  z-index: 3;
}
.dh-video {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: contain;
  opacity: 0;
  pointer-events: none;
}
.dh-video.dh-active {
  opacity: 1;
}
/* 口型同步画布：精确盖在视频嘴部位置（视频 704x1280 满幅缩放，
   258/704=36.65% 458/1280=35.78%，184/704=26.14% 114/1280=8.91%），
   画在视频之上、边框之下；不驱动时透明，露视频自己的嘴。 */
.dh-mouth {
  position: absolute;
  left: 36.65%;
  top: 35.78%;
  width: 26.14%;
  height: 8.91%;
  opacity: 0;
  pointer-events: none;
  z-index: 2;
  transition: opacity 0.08s linear;
}
.dh-mouth-on {
  opacity: 1;
}
/* 黑底视频融入舞台：不混色，只加青色投影光晕（全息感）。
   视频背景纯黑与舞台纯黑重合，无框无边。 */
.dh-sm .dh-video {
  filter: drop-shadow(0 0 14px rgba(0, 207, 255, 0.42));
}
.dh-lg .dh-video {
  filter: drop-shadow(0 0 20px rgba(0, 180, 255, 0.32));
}
</style>
