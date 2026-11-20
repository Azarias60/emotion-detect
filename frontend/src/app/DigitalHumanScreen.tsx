import { useEffect, useMemo, useRef, useState } from 'react'
import DigitalHumanPage, { type HumanView } from '../pages/DigitalHumanPage'
import { createCompanionSelection, type ObjectCollection, type ObjectSnapshot } from '../application/digitalHuman'
import { useCompanionSelection, useObjectCollection } from './useObjectCollections'
import { useCompanionLive } from './useCompanionLive'
import { useAvatarStudio } from './useAvatarStudio'
import { useVoiceClone } from './useVoiceClone'
import { useLlmSettings } from './useLlmSettings'
import type { DigitalHumanTransport } from '../domain/companionLive'
import type { StudioPort as AvatarStudioPort } from '../domain/avatarStudio'
import type { ClonePort } from '../domain/voiceClone'
import type { LlmSettingsPort } from '../domain/llmSettings'
import type { ObjectKind } from '../domain/digitalHuman'

/** 数字人页面：陪伴对话与实时口型走数字人引擎（WebRTC + WS 事件流，
    资料卡/推荐/动作/唤醒全部为引擎真实能力）；配置与素材集合走服务器网关；
    制作与音色克隆走产线后端（8081）；模型 Key 写引擎本机配置热生效。
    管理弹窗（配置/形象/音色/素材）保持服务器对象模型不变。*/
/** 会话支撑端口的结构契约；具体装配由组合根注入，类型不跨层导入。 */
export type ScreenGateways = {
  conversation: {
    expressUsage(kind: 'guide' | 'companion', active: boolean): Promise<void>
    send(kind: 'guide' | 'companion', text: string): Promise<{ reply: string }>
    restart(kind: 'guide' | 'companion'): Promise<void>
  }
  modelConfig: {
    read(): Promise<{ configured: boolean; masked: string }>
    write(apiKey: string): Promise<void>
  }
  production: {
    start(kind: 'avatar' | 'voice', input: Record<string, unknown>): Promise<{ taskId: string }>
    status(taskId: string): Promise<{ status: string; failure: string }>
    cancel(taskId: string): Promise<void>
    startRecording(): Promise<void>
    stopRecording(): Promise<{ taskId: string }>
    cancelRecording(): Promise<void>
  }
}

export default function DigitalHumanScreen({ gateways, collections, active, engineTransport, studioPort, clonePort, llmPort }: {
  gateways: ScreenGateways
  collections: Record<'configuration' | 'avatar' | 'voice' | 'material', ObjectCollection>
  active: boolean
  engineTransport: DigitalHumanTransport
  studioPort: AvatarStudioPort
  clonePort: ClonePort
  llmPort: LlmSettingsPort
}) {
  const selection = useMemo(() => createCompanionSelection(collections), [collections])
  const [message, setMessage] = useState('')
  const [keyState, setKeyState] = useState<{ configured: boolean; masked: string } | null>(null)

  const configuration = useObjectCollection(collections.configuration)
  const voiceCollection = useObjectCollection(collections.voice)
  const material = useObjectCollection(collections.material)
  const avatarCollection = useObjectCollection(collections.avatar)
  const selected = useCompanionSelection(selection)
  const resolved = useMemo(() => selection.resolve(), [selection, configuration, voiceCollection, avatarCollection])

  // ── 引擎实时会话（WebRTC 口型 + WS 事件流）：资料卡/推荐/动作/唤醒全在引擎侧 ──
  const live = useCompanionLive(engineTransport)
  // ── 制作产线与语音克隆（8081）──
  const studio = useAvatarStudio(studioPort)
  const voice = useVoiceClone(clonePort)
  // ── 模型 Key（引擎热配置）──
  const llm = useLlmSettings(llmPort)

  // 页面使用期：进入绑定会话使用条件（服务器对象集合语义保持不变）。
  useEffect(() => {
    if (!active) return
    void gateways.conversation.expressUsage('companion', true).catch(() => {})
    return () => { void gateways.conversation.expressUsage('companion', false).catch(() => {}) }
  }, [gateways, active])

  // 挂载：接回进行中的制作任务 + 加载语音克隆素材（引导句/官方音色）。
  // 产线跑在后端进程里，刷新页面/重新进页不丢进度——这条是"回来就能接着看"的关键。
  const studioResumeRef = useRef(studio.resumeRunning)
  studioResumeRef.current = studio.resumeRunning
  const voiceLoadRef = useRef(voice)
  voiceLoadRef.current = voice
  useEffect(() => {
    studioResumeRef.current()
    voiceLoadRef.current.loadSentences()
    voiceLoadRef.current.loadOfficial()
  }, [])

  // Key 状态读引擎（掩码）；弹窗保存也走引擎，下一句对话热生效。
  const llmReloadRef = useRef(llm.reload)
  llmReloadRef.current = llm.reload
  useEffect(() => {
    if (!active) return
    void llmReloadRef.current()
  }, [active])
  useEffect(() => {
    const config = llm.state.config
    if (config) setKeyState({ configured: config.hasKey, masked: config.keyHint || '未配置' })
  }, [llm.state.config])

  async function saveModelKey(apiKey: string) {
    const ok = await llm.save({ apiKey })
    if (ok) live.note(`模型 Key 已更新，下一句对话生效`)
    return ok
  }

  async function restart() {
    setMessage('')
    // 引擎侧软重置：清空本次对话/记忆/情绪/推荐，保留已唤醒状态
    live.resetSession()
    try { await gateways.conversation.restart('companion') } catch { /* 服务器会话仅为对象语义，可失败 */ }
  }

  async function startProduction(_kind: 'avatar' | 'voice', _input: Record<string, unknown>) {
    // 制作走「上传照片/直链 → 起名 → 产线」流程（制作弹窗内的 studio 表单），
    // 旧的“按 name 直接提交”入口不再使用。
  }

  const snapshots: Record<ObjectKind, ObjectSnapshot> = {
    configuration,
    avatar: avatarCollection,
    voice: voiceCollection,
    material,
  }

  const view: HumanView = {
    configs: configuration.rows,
    voices: voiceCollection.rows,
    avatars: avatarCollection.rows,
    materials: material.rows,
    selected,
    resolved,
    keyState,
    reply: '',
    message,
    sending: studio.state.creating || studio.state.uploading || voice.state.uploading,
    live: {
      state: live.state,
      onVideoReady: live.connect,
      onTapPlay: live.tapPlay,
      onToggleMic: live.toggleMic,
      onInterrupt: live.interrupt,
      onRetry: live.retry,
      onSend: live.sendText,
      dance: live.dance,
      refreshAssets: live.refreshAssets,
      selectPreset: live.selectPreset,
      switchVoice: live.switchVoice,
      deleteAvatar: live.deleteAvatar,
      clearRecommendation: live.clearRecommendation,
      note: live.note,
      onReset: () => { live.resetSession(); void restart() },
    },
    studio: {
      state: studio.state,
      uploadPhoto: studio.uploadPhoto,
      fetchPhotoByUrl: studio.fetchPhotoByUrl,
      create: studio.create,
      cancel: studio.cancel,
      reset: studio.reset,
    },
    voiceState: voice.state,
    voiceActions: {
      start: voice.start,
      stopAndUpload: voice.stopAndUpload,
      chooseOfficial: voice.chooseOfficial,
      pickSentence: voice.pickSentence,
      bindTo: voice.bindTo,
    },
    llm: {
      state: llm.state,
      save: llm.save,
    },
    management: {
      snapshots,
      onReferences: (kind, id) => collections[kind].references(id).then(found => found.references),
      onRemove: async (kind, id) => { await collections[kind].remove(id) },
      onMaterialCreate: async values => { await collections.material.create(values) },
    },
    actions: {
      send: () => { /* 文字交互已由 live 会话承载 */ },
      restart: () => { void restart() },
      startProduction,
      saveModelKey,
      selectConfiguration: id => { selection.selectConfiguration(id); live.note(`已选择数字人「${id}」`) },
      selectTemporaryVoice: id => selection.selectTemporaryVoice(id),
      refreshObjects: async () => {
        await Promise.all(Object.values(collections).map(collection => collection.refresh()))
        await live.refreshAssets()
      },
    },
  }
  return <DigitalHumanPage view={view} />
}
