import { onBeforeUnmount, ref } from 'vue'
import { detectionClient as http, responseData } from '../../api/http.js'

// 与 useAvatarStudio 同源：dev 走 /python-api 代理，生产可用 VITE_PY_API_URL 覆盖
const API = (import.meta.env.VITE_PY_API_URL || '/python-api').replace(/\/+$/, '')

// 录音采集 Worklet：麦克风 → 16kHz 单声道 Float32 分片推给主线程。
// 不用 MediaRecorder：它产出 webm/opus 容器，后端质检只收真 WAV（RIFF），
// 且文件名伪装成 .wav 也会在 wave.open 处直接失败（2026-11-13 重写）。
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

const TARGET_SR = 16000

/** Float32 分片 → 16kHz 单声道 16bit WAV Blob（44 字节头 + PCM）。 */
function encodeWav(chunks, sampleRate) {
  const total = chunks.reduce((sum, part) => sum + part.length, 0)
  const buffer = new ArrayBuffer(44 + total * 2)
  const view = new DataView(buffer)
  const ascii = (offset, text) => {
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
        // NaN/Inf 显式归零：Math clamp 对 NaN 无效（NaN<0 为 false 会走进
        // NaN*0x7fff），依赖 ToInt16 的 NaN→0 太隐晦（DeepSeek 审查 P1-5）
        const raw = part[i]
        const sample = Number.isFinite(raw) ? Math.max(-1, Math.min(1, raw)) : 0
        view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true)
        offset += 2
      }
    }
  return new Blob([buffer], { type: 'audio/wav' })
}

/** 上下文采样率不是 16k 时做线性插值（浏览器普遍支持 16k，这条是兜底）。 */
function resampleTo16k(chunks, fromRate) {
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

/** 语音克隆录制状态与上传：录一句引导句 → 上传 → 绑定到照片数字人。 */
export function useVoiceClone() {
  const recording = ref(false)
  const seconds = ref(0)
  const uploading = ref(false)
  const error = ref('')
  const done = ref('')
  /** 已上传的音色 id + 绑定目标数字人 id */
  const voiceId = ref('')
  const boundAvatar = ref('')
  const sentence = ref('')
  const sentences = ref([])
  /** 未录音时的官方备用音色选择（'official_male' | 'official_female'） */
  const officialVoice = ref('')
  const officialVoices = ref([])

  let micStream = null
  let audioCtx = null
  let micSource = null
  let micNode = null
  let micSink = null
  let workletReady = false
  let pcmChunks = []
  let stopping = null
  let timer = null
  let startAt = 0
  let starting = false
  let stopRequested = false // starting 期间用户点了停止：开麦成功后立即停
  let disposed = false

  function stopTimer() {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  }

  function releaseMic() {
    try {
      micNode?.disconnect()
      micSource?.disconnect()
      micSink?.disconnect()
      micStream?.getTracks().forEach((track) => track.stop())
    } catch {
      /* 已断开时忽略 */
    }
    micNode = null
    micSource = null
    micSink = null
    micStream = null
    if (audioCtx && audioCtx.state !== 'closed') {
      audioCtx.close().catch(() => {})
    }
    audioCtx = null
    // AudioWorklet 定义注册在具体的 AudioContext 上，不是全局的：ctx 关了之后
    // 必须重置 ready 标志，否则二次进入页面时 new AudioWorkletNode 会抛
    // "not defined"（2026-11-13 DeepSeek 审查 P0-1）
    workletReady = false
  }

  onBeforeUnmount(() => {
    // 录音中/开麦中/上传中离开页面：停表、停轨道、停上下文。
    // 旧实现整个钩子缺失——浏览器麦克风指示灯会一直亮着（隐私事故）。
    // 无条件 releaseMic（幂等）+ destroyed 标志让在途的 getUserMedia 返回后
    // 直接收手，不在已卸载的组件上建上下文（2026-11-13 DeepSeek 审查 P1-3）
    disposed = true
    stopTimer()
    if (recording.value) recording.value = false
    releaseMic()
  })

  function pickSentence() {
    const list = sentences.value.length ? sentences.value : ['']
    sentence.value = list[Math.floor(Math.random() * list.length)]
  }

  async function loadSentences() {
    try {
      const data = await responseData(http.get('/dh/voice/sentences'))
      sentences.value = data.sentences || []
    } catch {
      sentences.value = []
    }
    // 失败也重挑一句：否则 sentence 停留在上一次的值（或空串），
    // 引导句区域显示过期内容（DeepSeek 审查 P2-11）
    pickSentence()
  }

  async function loadOfficial() {
    try {
      const data = await responseData(http.get('/dh/voice/official'))
      officialVoices.value = data.voices || []
    } catch {
      officialVoices.value = []
    }
  }

  /** 选官方备用音色：avatarId 非空立即绑定；为空则挂起，制作完成后由视图应用 */
  async function chooseOfficial(voice, avatarId = '') {
    if (!voice) return false
    officialVoice.value = voice
    if (!avatarId) {
      done.value = `已选官方音色（${voice === 'official_male' ? '男声' : '女声'}），制作完成后自动使用`
      return true
    }
    uploading.value = true
    try {
      const ok = await bindVoiceId(voice, avatarId)
      if (ok) done.value = `官方音色已绑定到「${avatarId}」`
      return ok
    } finally {
      uploading.value = false
    }
  }

  async function startRecording() {
    if (starting || recording.value) return
    starting = true
    stopRequested = false
    error.value = ''
    done.value = ''
    officialVoice.value = ''
    try {
      micStream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
      })
      // 卸载/取消竞态：等麦克风的途中组件没了或用户点了停止，就地收手
      if (disposed || stopRequested) {
        releaseMic()
        return
      }
      audioCtx = new AudioContext({ sampleRate: TARGET_SR })
      if (!workletReady) {
        const workletUrl = URL.createObjectURL(new Blob([RECORDER_WORKLET], { type: 'text/javascript' }))
        try {
          await audioCtx.audioWorklet.addModule(workletUrl)
          workletReady = true
        } finally {
          URL.revokeObjectURL(workletUrl)
        }
      }
      pcmChunks = []
      micNode = new AudioWorkletNode(audioCtx, 'voice-clone-recorder')
      micNode.port.onmessage = (message) => {
        if (recording.value) pcmChunks.push(new Float32Array(message.data))
      }
      micSource = audioCtx.createMediaStreamSource(micStream)
      // 零增益下沉：拉活 Worklet 又不外放，避免回声
      micSink = audioCtx.createGain()
      micSink.gain.value = 0
      micSource.connect(micNode)
      micNode.connect(micSink)
      micSink.connect(audioCtx.destination)
      recording.value = true
      startAt = Date.now()
      timer = setInterval(() => {
        seconds.value = Math.floor((Date.now() - startAt) / 1000)
        if (seconds.value >= 15) void stopRecording()
      }, 500)
    } catch (err) {
      releaseMic()
      error.value = `无法开启麦克风：${err.message || err}`
    } finally {
      starting = false
    }
  }

  /** 停录并上传；avatarId 为空则只存音色不绑定。 */
  async function stopRecording(avatarId = '') {
    if (!recording.value) {
      // 开麦进行中（getUserMedia 还没返回）：记一笔，开完立刻停
      if (starting) stopRequested = true
      return null
    }
    if (stopping) return null
    stopping = (async () => {
      stopTimer()
      recording.value = false
      const rate = audioCtx ? audioCtx.sampleRate : TARGET_SR
      const chunks = pcmChunks
      pcmChunks = []
      releaseMic()
      seconds.value = 0
      const total = chunks.reduce((sum, part) => sum + part.length, 0)
      // 空录音要在编码前按样本数判断：只有 44 字节头的 WAV size 也非 0，
      // 走后端质检会拿到一句莫名其妙的格式错误（DeepSeek 审查 P1-4）
      if (!total) {
        error.value = '录音为空，请重试'
        return null
      }
      const prepared = resampleTo16k(chunks, rate)
      const audio = encodeWav(prepared, TARGET_SR)
      if (!audio || !audio.size) {
        error.value = '录音为空，请重试'
        return null
      }
      uploading.value = true
      try {
        const form = new FormData()
        form.append('audio', audio, 'voice.wav')
        const res = await fetch(`${API}/dh/voice/upload`, { method: 'POST', body: form })
        const body = await res.json().catch(() => ({}))
        if (!res.ok) throw new Error(body.detail || `上传失败 HTTP ${res.status}`)
        const data = body.data || {}
        voiceId.value = data.voice_id
        if (avatarId) {
          const bind = await fetch(`${API}/dh/voice/bind`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ voice_id: data.voice_id, avatar_id: avatarId }),
          })
          const bindBody = await bind.json().catch(() => ({}))
          if (!bind.ok) throw new Error(bindBody.detail || `绑定失败 HTTP ${bind.status}`)
          void bindBody
          boundAvatar.value = avatarId
          done.value = `音色已上传并绑定到「${avatarId}」，刷新页面后在数字人列表选择即可使用`
        } else {
          done.value = `音色已上传（${data.info?.duration ?? '?'} 秒），制作数字人完成后可绑定`
        }
        return data.voice_id
      } catch (err) {
        error.value = err.message || '上传失败'
        // 失败时清掉上一次的成功文案，避免新旧提示同屏（DeepSeek 审查 P1-7）
        done.value = ''
        return null
      } finally {
        uploading.value = false
      }
    })()
    try {
      return await stopping
    } finally {
      stopping = null
    }
  }

  async function bindVoiceId(vid, avatarId) {
    try {
      const res = await fetch(`${API}/dh/voice/bind`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ voice_id: vid, avatar_id: avatarId }),
      })
      const body = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(body.detail || `绑定失败 HTTP ${res.status}`)
      boundAvatar.value = avatarId
      return true
    } catch (err) {
      error.value = err.message || '绑定失败'
      return false
    }
  }

  async function bindVoice(avatarId) {
    if (!voiceId.value) return false
    const ok = await bindVoiceId(voiceId.value, avatarId)
    if (ok) done.value = `音色已绑定到「${avatarId}」`
    return ok
  }

  return {
    recording, seconds, uploading, error, done,
    voiceId, boundAvatar, sentence, sentences,
    officialVoice, officialVoices,
    loadSentences, loadOfficial, pickSentence, startRecording, stopRecording,
    bindVoice, chooseOfficial,
  }
}
