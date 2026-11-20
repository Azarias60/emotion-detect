/**
 * 语音克隆领域模型：读一句引导句 → 录音上传 → 绑定照片数字人（零样本克隆）。
 *
 * 后端（Emotion-Detect-py，8081）：
 *   GET  /dh/voice/sentences   引导句列表
 *   GET  /dh/voice/official    官方备用音色
 *   POST /dh/voice/upload      上传 WAV，返回 voice_id（后端质检时长/能量）
 *   POST /dh/voice/bind        把音色绑到指定数字人
 * 录音采集与编码在 api/voiceClone.ts（浏览器 API 只允许在基础设施层）。
 */

/** 官方备用音色（CosyVoice 一男一女）。 */
export interface OfficialVoice {
  id: string
  label: string
  desc?: string
  available?: boolean
}

export interface VoiceCloneState {
  recording: boolean
  seconds: number
  uploading: boolean
  error: string
  done: string
  /** 已上传的音色 id */
  voiceId: string
  /** 已绑定到的数字人 id（空 = 还没绑定，等待制作完成后自动绑） */
  boundAvatar: string
  /** 当前引导句 */
  sentence: string
  sentences: string[]
  /** 未录音时的官方备用音色选择 */
  officialVoice: string
  officialVoices: OfficialVoice[]
}

export const initialVoiceCloneState: VoiceCloneState = {
  recording: false,
  seconds: 0,
  uploading: false,
  error: '',
  done: '',
  voiceId: '',
  boundAvatar: '',
  sentence: '',
  sentences: [],
  officialVoice: '',
  officialVoices: [],
}

/** 单句最长录制时长：够质检用，也不逼用户念长文。 */
export const MAX_RECORD_SECONDS = 15

export const OFFICIAL_LABEL: Record<string, string> = {
  official_male: '男声',
  official_female: '女声',
}

/** 语音克隆平台端口：录音与网络在 api/voiceClone.ts。 */
export interface ClonePort {
  sentences(): Promise<string[]>
  officialVoices(): Promise<OfficialVoice[]>
  /** 开麦并开始采集（重复调用无害；失败抛错由应用层给文案） */
  startRecording(): Promise<void>
  /** 停止采集并编码为 16kHz WAV；空录音返回 null（应用层给"录音为空"提示） */
  stopRecording(): Promise<{ wav: Blob; seconds: number } | null>
  upload(wav: Blob): Promise<{ voiceId: string; duration?: number }>
  bind(voiceId: string, avatarId: string): Promise<void>
}

/** 随机挑一句引导句（列表为空时退回空串）。 */
export function pickSentence(sentences: string[]): string {
  if (!sentences.length) return ''
  return sentences[Math.floor(Math.random() * sentences.length)]
}
