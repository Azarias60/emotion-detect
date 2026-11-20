import { useEffect, useRef } from 'react'

/**
 * 动画数字人暖暖：5 个动作段（idle1/2/3、greet、talk）+ 20 条过渡片的
 * 视频状态机（逻辑移植自原 Vue 版 DigitalHumanAvatar，与 LiveTalking
 * state_machine 同源）。
 *
 * - 待机：循环播随机 idle，段末用 idle↔idle 过渡片切换
 * - 首次说话：硬切 greet（与语音同帧），greet 播完走 greet2talk → talk
 * - 再次说话：硬切 idle2talk 进 talk；说话中 talk 播完用 talk2talk 续
 * - 说完：硬切 talk2idle 回待机（嘴随声音同帧停）
 *
 * 双 video 层缓冲：当前段在激活层，下一段预载到后台层；切换只激活已就绪
 * 的后台层，可见层的 src 永不中途替换——任何情况下都不出现加载黑帧。
 */
const IDLES = ['idle1', 'idle2', 'idle3'] as const
type Segment = (typeof IDLES)[number] | 'greet' | 'talk'

const SEG = (name: string) => `/digital-human/${name}.mp4`
const TRANS = (from: string, to: string) => `/digital-human/T_${from}2${to}.mp4`
/** 呼吸感定格：idle 段末偶尔停 1~2 秒再动，不是一直小幅乱动 */
const HOLD_CHANCE = 0.35

/**
 * 段视频一律先 fetch 成 blob 再喂给 <video>。
 *
 * 原因（2026-11-20 实测）：ZCode 应用内浏览器（Electron webview）的媒体
 * HTTP 加载器会把 <video> 的请求无限期挂起——play() 受理了但 readyState
 * 永远 0，换新地址、手动 load()+play()、强制重绘都救不活；而同一个文件的
 * fetch() 毫秒级返回、blob src 的 video 立即出画。所以不再让媒体栈自己发
 * 网络请求：段按路径缓存成 blob URL（全部 25 段约 17MB，一次取完复用），
 * 普通浏览器上这条路径同样成立。
 */
const segmentBlobUrls = new Map<string, Promise<string>>()
function blobUrlFor(path: string): Promise<string> {
  let entry = segmentBlobUrls.get(path)
  if (!entry) {
    entry = fetch(path)
      .then((response) => {
        if (!response.ok) throw new Error(`段视频加载失败 HTTP ${response.status}`)
        return response.blob().then((blob) => URL.createObjectURL(blob))
      })
      .catch(() => path) // fetch 失败退回直连地址：正常浏览器本来就能直接播
    segmentBlobUrls.set(path, entry)
  }
  return entry
}

function pickIdle(exclude?: string): Segment {
  const choices = IDLES.filter((name) => name !== exclude)
  return choices[Math.floor(Math.random() * choices.length)] ?? IDLES[0]
}

interface AvatarApi {
  speakStart: () => void
  speakEnd: () => void
  destroy: () => void
}

export function GuideAvatar({ speaking }: { speaking: boolean }) {
  const rootRef = useRef<HTMLDivElement>(null)
  const videoA = useRef<HTMLVideoElement>(null)
  const videoB = useRef<HTMLVideoElement>(null)
  const api = useRef<AvatarApi | null>(null)

  useEffect(() => {
    const a = videoA.current
    const b = videoB.current
    const root = rootRef.current
    if (!a || !b || !root) return

    let active: 'a' | 'b' = 'a'
    let current: Segment = 'idle1'
    let inTransition = false
    let transitionTarget: Segment | null = null
    let speakingNow = false
    let mustReachTalk = false
    let firstUtteranceDone = false
    let wantGreet = false
    let destroyed = false
    let retryBound = false
    let holdTimer: number | null = null
    // 每层当前装载的段路径（元素 src 是 blob URL，比较用路径）
    const layerPath: Record<'a' | 'b', string | null> = { a: null, b: null }
    const layerCommit: Record<'a' | 'b', string | null> = { a: null, b: null }

    const el = (layer: 'a' | 'b') => (layer === 'a' ? a : b)
    const other = (layer: 'a' | 'b'): 'a' | 'b' => (layer === 'a' ? 'b' : 'a')

    const setState = (name: Segment) => {
      current = name
      root.dataset.motion = name
    }
    const setActive = (layer: 'a' | 'b') => {
      active = layer
      a.classList.toggle('is-active', layer === 'a')
      b.classList.toggle('is-active', layer === 'b')
    }
    const clearHold = () => {
      if (holdTimer !== null) {
        clearTimeout(holdTimer)
        holdTimer = null
      }
    }

    const retryPlay = () => {
      const v = el(active)
      if (v.paused && !v.ended) v.play().catch(() => undefined)
    }
    // 看门狗：blob 解码极少数情况下也会停摆（rs 迟迟 0），重 load()+play()
    // 重新走本地解码即可恢复——只动本地 blob，不再涉及网络栈。
    const kick = (layer: 'a' | 'b') => {
      const v = el(layer)
      if (!layerPath[layer]) return
      v.load()
      v.play().catch(() => undefined)
    }
    const watchdog = window.setInterval(() => {
      if (destroyed) return
      const activeV = el(active)
      if (layerPath[active] && activeV.readyState < 2) {
        kick(active)
        return
      }
      // 预载层停摆：段切换永远等不到 loadeddata，状态机会卡死在定格上
      const backLayer = other(active)
      if (layerCommit[backLayer] && el(backLayer).readyState === 0) kick(backLayer)
    }, 2500)
    const bindRetryPlay = () => {
      if (retryBound) return
      retryBound = true
      document.addEventListener('pointerdown', retryPlay, { once: true })
      document.addEventListener('keydown', retryPlay, { once: true })
    }
    // 切回标签页恢复播放（浏览器会在后台标签页暂停视频）
    const onVisibility = () => { if (!document.hidden) retryPlay() }
    document.addEventListener('visibilitychange', onVisibility)

    const setLayerSrc = (layer: 'a' | 'b', path: string) => {
      if (layerPath[layer] === path) return
      layerPath[layer] = path
      void blobUrlFor(path).then((url) => {
        // 异步到位时目标可能已变（快速换段）：以 layerPath 为准，旧的丢弃
        if (destroyed || layerPath[layer] !== path) return
        const v = el(layer)
        v.src = url
        v.load()
      })
    }
    const preload = (src: string) => {
      const layer = other(active)
      if (layerCommit[layer]) return
      setLayerSrc(layer, src)
    }
    const switchTo = (layer: 'a' | 'b', src: string) => {
      const v = el(layer)
      layerCommit[layer] = null
      setActive(layer)
      v.currentTime = 0
      v.play().catch(() => bindRetryPlay())
    }
    const playNext = (src: string) => {
      const back = other(active)
      const vBack = el(back)
      layerCommit[back] = src
      if (layerPath[back] === src && vBack.readyState >= 3) {
        switchTo(back, src)
        return
      }
      vBack.addEventListener('loadeddata', () => {
        layerCommit[back] = null
        if (!destroyed) switchTo(back, src)
      }, { once: true })
      setLayerSrc(back, src)
    }

    const nextAfter = (name: Segment): string => {
      if (name === 'talk') return speakingNow ? TRANS('talk', 'talk') : TRANS('talk', pickIdle())
      if ((IDLES as readonly string[]).includes(name)) {
        return speakingNow ? TRANS(name, 'talk') : TRANS(name, pickIdle(name))
      }
      return TRANS('greet', 'talk')
    }
    const activate = (name: Segment) => {
      if (name === 'talk') mustReachTalk = false
      inTransition = false
      transitionTarget = null
      setState(name)
      playNext(SEG(name))
      preload(nextAfter(name))
    }
    const triggerTransition = (from: Segment, to: Segment) => {
      if (to === 'talk') mustReachTalk = false
      if (from === to) {
        if (from === 'talk') {
          // talk 段首尾姿势不同，直接重播是硬跳变：必须走 T_talk2talk
          inTransition = true
          transitionTarget = 'talk'
          root.dataset.motion = 'talk→talk'
          playNext(TRANS('talk', 'talk'))
          preload(SEG('talk'))
          return
        }
        activate(to)
        return
      }
      inTransition = true
      transitionTarget = to
      root.dataset.motion = `${from}→${to}`
      playNext(TRANS(from, to))
      preload(SEG(to))
    }

    const routeFromIdle = () => {
      if (speakingNow || mustReachTalk) {
        mustReachTalk = false
        if (wantGreet) {
          wantGreet = false
          triggerTransition(current, 'greet')
        } else {
          triggerTransition(current, 'talk')
        }
      } else {
        triggerTransition(current, pickIdle(current))
      }
    }

    const onEnded = (layer: 'a' | 'b') => {
      if (layer !== active || destroyed) return
      if (inTransition) {
        const target = transitionTarget
        if (!target) return
        activate(target)
        return
      }
      const state = current
      if ((IDLES as readonly string[]).includes(state)) {
        if (holdTimer === null && Math.random() < HOLD_CHANCE) {
          holdTimer = window.setTimeout(() => {
            holdTimer = null
            if (!destroyed && current === state && !inTransition) routeFromIdle()
          }, 1000 + Math.random() * 1000)
          return
        }
        routeFromIdle()
        return
      }
      if (state === 'greet') {
        if (speakingNow || mustReachTalk) {
          mustReachTalk = false
          triggerTransition('greet', 'talk')
        } else {
          triggerTransition('greet', pickIdle())
        }
        return
      }
      if (state === 'talk') {
        if (speakingNow) triggerTransition('talk', 'talk')
        else {
          mustReachTalk = false
          triggerTransition('talk', pickIdle())
        }
      }
    }

    /** 首句语音起播：硬切 greet（与声音同帧开始），greet 播完自动进 talk */
    const greetNow = () => {
      speakingNow = true
      mustReachTalk = true
      firstUtteranceDone = true
      wantGreet = false
      clearHold()
      if (current === 'greet' || current === 'talk') return
      activate('greet')
    }
    /** 后续语音起播：硬切 idle2talk 进说话链；已在 talk 循环则无缝继续 */
    const talkNow = () => {
      speakingNow = true
      mustReachTalk = true
      firstUtteranceDone = true
      wantGreet = false
      clearHold()
      if (current === 'talk') return
      if (inTransition && transitionTarget === 'talk') return
      triggerTransition(current, 'talk')
    }
    /** 声音播完：硬切出说话链，不留"张嘴没声音"的尾巴 */
    const stopTalkingNow = () => {
      speakingNow = false
      mustReachTalk = false
      wantGreet = false
      clearHold()
      if (inTransition) {
        activate(pickIdle())
        return
      }
      if (current === 'talk' || current === 'greet') triggerTransition(current, pickIdle())
    }

    const onEndedA = () => onEnded('a')
    const onEndedB = () => onEnded('b')
    a.addEventListener('ended', onEndedA)
    b.addEventListener('ended', onEndedB)

    // 首帧：A 层装入 idle1（经 blob 化）。loadeddata 在 blob src 就绪后触发
    // startFirstFrame；StrictMode 重挂时若段已在缓冲则直接起播。
    setState(current)
    setActive('a')
    const startFirstFrame = () => {
      if (destroyed) return
      setActive('a')
      a.currentTime = 0
      bindRetryPlay()
      a.play().catch(() => undefined)
    }
    a.addEventListener('loadeddata', startFirstFrame, { once: true })
    setLayerSrc('a', SEG(current))
    if (a.readyState >= 2) startFirstFrame()
    else a.play().catch(() => bindRetryPlay())

    api.current = {
      speakStart: () => {
        if (firstUtteranceDone) talkNow()
        else greetNow()
      },
      speakEnd: stopTalkingNow,
      destroy: () => {
        destroyed = true
        clearHold()
        clearInterval(watchdog)
        document.removeEventListener('visibilitychange', onVisibility)
        a.removeEventListener('loadeddata', startFirstFrame)
        a.removeEventListener('ended', onEndedA)
        b.removeEventListener('ended', onEndedB)
      },
    }
    return () => {
      api.current?.destroy()
      api.current = null
    }
  }, [])

  // 语音起播/播完边沿驱动动作链（与语音同帧，见组件头注释）
  useEffect(() => {
    const handle = api.current
    if (!handle) return
    if (speaking) handle.speakStart()
    else handle.speakEnd()
  }, [speaking])

  return <div className="gav" ref={rootRef} data-motion="idle1" data-speaking={speaking || undefined}>
    <video ref={videoA} className="gav-video is-active" muted playsInline preload="auto" aria-hidden="true" />
    <video ref={videoB} className="gav-video" muted playsInline preload="auto" aria-hidden="true" />
  </div>
}
