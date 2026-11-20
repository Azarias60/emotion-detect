import { useRef, useState } from 'react'
import { Alert, Badge, Button, Descriptions, Form, Input, Modal, Popconfirm, Progress, Select, Tabs, Tag, Typography } from 'antd'
import { Upload as UploadIcon } from 'lucide-react'
import { DigitalHumanStage as CompanionStage } from '../components/CompanionStage'
import { CompanionPicker } from '../components/CompanionPicker'
import { CompanionChat } from '../components/CompanionChat'
import { CompanionAbility, CompanionStudio } from '../components/CompanionSide'
import type { DigitalHumanState } from '../domain/companionLive'
import type { StudioState } from '../domain/avatarStudio'
import { stageLabel } from '../domain/avatarStudio'
import type { VoiceCloneState } from '../domain/voiceClone'
import type { LlmSettingsState } from '../domain/llmSettings'
import { WorkError } from '../components/WorkSurface'
import type { DigitalHumanObject, ObjectKind } from '../domain/digitalHuman'

/** 页面只呈现数字人事实与意图；集合读取、引用确认与命令由屏幕提供。 */
export type ManagedCollectionSnapshot = { rows: DigitalHumanObject[]; synced: boolean }

export type HumanView = {
  configs: DigitalHumanObject[]
  voices: DigitalHumanObject[]
  avatars: DigitalHumanObject[]
  materials: DigitalHumanObject[]
  selected: { selectedConfigId: string; temporaryVoiceId: string }
  resolved: { configuration: DigitalHumanObject | null; avatar: DigitalHumanObject | null; voice: DigitalHumanObject | null }
  keyState: { configured: boolean; masked: string } | null
  reply: string
  message: string
  sending: boolean
  /** 数字人引擎实时会话：WebRTC 口型 + 流式对话 + 资料卡/推荐/动作/唤醒 */
  live: {
    state: DigitalHumanState
    onVideoReady: (video: HTMLVideoElement) => void
    onTapPlay: () => void
    onToggleMic: () => void
    onInterrupt: () => void
    onRetry: () => void
    onSend: (text: string) => void
    onReset: () => void
    dance: () => void
    refreshAssets: () => void
    selectPreset: (id: string) => void
    switchVoice: (id: string) => void
    deleteAvatar: (id: string, name: string) => Promise<boolean>
    note: (text: string) => void
    clearRecommendation: () => void
  }
  /** 照片制作数字人（8081 产线） */
  studio: {
    state: StudioState
    uploadPhoto: (file: File) => Promise<string>
    fetchPhotoByUrl: (url: string) => Promise<string>
    create: (jobId: string, name: string, displayName: string) => Promise<boolean>
    cancel: () => Promise<boolean>
    reset: () => void
  }
  /** 语音克隆（8081） */
  voiceState: VoiceCloneState
  voiceActions: {
    start: () => void
    stopAndUpload: (avatarId?: string) => void
    chooseOfficial: (voiceId: string, avatarId?: string) => void
    pickSentence: () => void
    bindTo: (avatarId: string) => void
  }
  /** 模型 Key（引擎热配置） */
  llm: {
    state: LlmSettingsState
    save: (draft: { apiKey: string }) => Promise<boolean>
  }
  management: {
    snapshots: Record<ObjectKind, { rows: DigitalHumanObject[]; synced: boolean }>
    onReferences: (kind: ObjectKind, id: number) => Promise<{ kind: string; id: number }[]>
    onRemove: (kind: ObjectKind, id: number) => Promise<void>
    onMaterialCreate: (values: Record<string, unknown>) => Promise<unknown>
  }
  actions: {
    send: (text: string) => void
    restart: () => void
    startProduction: (kind: 'avatar' | 'voice', input: Record<string, unknown>) => Promise<void>
    saveModelKey: (key: string) => Promise<boolean>
    selectConfiguration: (id: string) => void
    selectTemporaryVoice: (id: string) => void
    refreshObjects: () => Promise<void>
  }
}

const KIND_LABELS = { configuration: '配置', avatar: '形象', voice: '音色', material: '素材' } as const

function ManagementModal({ view, onClose }: { view: HumanView; onClose: () => void }) {
  const [tab, setTab] = useState<ObjectKind>('configuration')
  const [pending, setPending] = useState<{ kind: ObjectKind; id: number; references: { kind: string; id: number }[] } | null>(null)
  const rows = view.management.snapshots[tab].rows
  async function confirmRemove(item: DigitalHumanObject) {
    const references = await view.management.onReferences(tab, Number(item.id))
    setPending({ kind: tab, id: Number(item.id), references })
  }
  const activePanel = <>
    {rows.map(item => (
      <Descriptions key={String(item.id)} column={1} size="small" bordered items={[{
        key: String(item.id),
        label: `${item.name ?? String(item.id)}`,
        children: <>
          <span>{Object.entries(item).filter(([key]) => !['id', 'name'].includes(key))
            .map(([key, value]) => `${key}: ${String(value)}`).join(' · ') || '无引用'}</span>
          <Popconfirm title="确认删除该对象？删除只结束目标对象与自身资源，引用者保留原引用。"
            onConfirm={() => { void confirmRemove(item) }}>
            <Button type="link" size="small" danger>删除</Button>
          </Popconfirm>
        </>,
      }]} />
    ))}
    {rows.length === 0 && <Alert type="info" showIcon title="该类型暂无对象。" />}
    {pending?.kind === tab && <Alert type="warning" showIcon
      title={`引用者：${pending.references.length === 0 ? '无（可安全删除）' : pending.references.map(row => `${row.kind} #${row.id}`).join('、')}`}
      action={<Popconfirm title="确认执行删除？" onConfirm={() => { void view.management.onRemove(pending.kind, pending.id); setPending(null) }}>
        <Button size="small" type="primary" danger>确认删除</Button>
      </Popconfirm>} />}
  </>
  return <Modal open onCancel={onClose} footer={null} title="管理数字人对象" width={640} destroyOnHidden>
    <Tabs activeKey={tab} onChange={kind => { setTab(kind as ObjectKind); setPending(null) }}
      items={Object.entries(KIND_LABELS).map(([key, label]) => ({
        key, label, children: key === tab ? activePanel : null,
      }))} />
  </Modal>
}

/** 制作数字人（8081 产线）：上传/直链 → 起名 → 进度 → 完成，界面即 5174 的 CompanionStudio。 */
function ProductionModal({ view, onClose }: { view: HumanView; onClose: () => void }) {
  const live = view.live
  const voice = view.voiceState
  const studio = view.studio
  const running = studio.state.status === 'running'
  const studioBusy = studio.state.uploading || studio.state.creating || running
  const canDance = (live.state.assets?.avatars ?? []).some(item => item.id === (live.state.currentAvatar || live.state.assets?.defaultAvatar) && item.dance)
  const fileRef = useRef<HTMLInputElement>(null)
  const [cancelling, setCancelling] = useState(false)
  async function pickAndUpload() {
    const input = fileRef.current
    const file = input?.files?.[0]
    if (input) input.value = ''
    if (file) await studio.uploadPhoto(file)
  }
  return <Modal open onCancel={onClose} footer={null} title="制作数字人 · 音色克隆 · 舞蹈" width={720} destroyOnHidden>
    <CompanionStudio state={live.state} voice={voice} studio={studio.state}
      studioBusy={studioBusy}
      onPickPhoto={() => fileRef.current?.click()}
      onFetchUrl={url => { void studio.fetchPhotoByUrl(url) }}
      onConfirmCreate={name => {
        const ascii = `user${Date.now().toString(36)}`
        void studio.create(studio.state.jobId, ascii, name.trim() || '我的数字人')
      }}
      onCancelJob={() => {
        if (!window.confirm('确定取消这次制作吗？已生成的中间产物会保留，但本次任务会停止。')) return
        setCancelling(true)
        void studio.cancel().finally(() => setCancelling(false))
      }}
      cancelling={cancelling}
      onDismissStudio={studio.reset}
      onGenerate={name => live.note(`已开始生成人物 ${name}`)}
      onDance={live.dance}
      canDance={(live.state.assets?.avatars ?? []).some(item => item.id === (live.state.currentAvatar || live.state.assets?.defaultAvatar) && item.dance)}
      onRecordVoice={view.voiceActions.start}
      onStopRecord={() => view.voiceActions.stopAndUpload()}
      onPickSentence={view.voiceActions.pickSentence}
      onPickOfficial={voiceId => view.voiceActions.chooseOfficial(voiceId)}
      onBindVoice={avatarId => { void view.voiceActions.bindTo(avatarId) }} />
    <input ref={fileRef} type="file" accept="image/jpeg,image/png,image/webp" hidden onChange={() => void pickAndUpload()} />
    {running && <Progress percent={studio.state.total > 0 && studio.state.stageIndex >= 0
      ? Math.round(((studio.state.stageIndex + 1) / studio.state.total) * 100) : 0} status="active" />}
    {running && <Typography.Text type="secondary">
      {stageLabel(studio.state.stage)}（{studio.state.stageIndex + 1}/{studio.state.total}）
      {studio.state.message ? `—— ${studio.state.message}` : ''}
    </Typography.Text>}
  </Modal>
}

/** 模型 Key（引擎热配置）：掩码读取，保存后下一句对话生效。 */
function ModelKeyModal({ view, onClose }: { view: HumanView; onClose: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function submit({ apiKey }: { apiKey?: string }) {
    if (!apiKey) return
    setBusy(true)
    setError('')
    try {
      const ok = await view.llm.save({ apiKey })
      if (!ok) throw new Error(view.llm.state.error || '保存失败')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '保存失败')
    } finally {
      setBusy(false)
    }
  }
  return <Modal open onCancel={onClose} footer={null} title="模型 Key 设置" width={480} destroyOnHidden>
    <Descriptions column={1} size="small" items={[{
      key: 'state', label: '当前状态',
      children: view.llm.state.config?.hasKey ? `已配置（${view.llm.state.config.keyHint}）` : '未配置',
    }, {
      key: 'effect', label: '生效时机',
      children: '写引擎本机配置热生效：下一句对话即使用新 Key，现有上下文保留',
    }]} />
    <Form layout="vertical" onFinish={submit}>
      <Form.Item name="apiKey" label="新 Key" rules={[{ required: true, message: '请输入模型 Key' }]}>
        <Input.Password placeholder="输入新的模型 Key" />
      </Form.Item>
      <Button type="primary" htmlType="submit" loading={busy}>保存并生效</Button>
    </Form>
    {error && <Alert type="error" showIcon title={error} />}
  </Modal>
}

const FORM = { Form, Input, Select } as const

export default function DigitalHumanPage({ view }: { view: HumanView }) {
  void FORM
  const [modal, setModal] = useState<'none' | 'management' | 'production' | 'key'>('none')
  const [refreshing, setRefreshing] = useState(false)
  const [refreshError, setRefreshError] = useState('')
  const [voiceOpen, setVoiceOpen] = useState(false)
  const live = view.live
  const connected = live.state.connection === 'connected'
  const configurationId = view.selected.selectedConfigId
    || (view.configs[0] ? String(view.configs[0].id) : '')
  const voiceId = view.selected.temporaryVoiceId
    || String(view.configs.find(item => String(item.id) === configurationId)?.voice ?? '')
  async function refreshObjects() {
    setRefreshing(true)
    setRefreshError('')
    try {
      await view.actions.refreshObjects()
    } catch (error) {
      setRefreshError(error instanceof Error ? error.message : '无法重新读取数字人对象')
    } finally {
      setRefreshing(false)
    }
  }
  // 显示名解析：引擎预设表里查中文名，避免把 e2etest01 / default 这类内部 ID 露给用户；
  // 引擎已连上默认形象但预设表里没有对应项时显示「默认形象」，不写「未配置」误导用户
  const currentAvatarId = live.state.currentAvatar || live.state.assets?.defaultAvatar || ''
  const avatarName = live.state.assets?.presets.find(item => item.avatar === currentAvatarId)?.name
    ?? (currentAvatarId ? currentAvatarId : '默认形象')
  const voiceName = live.state.assets?.voices.find(item => item.id === live.state.assets?.activeVoice)?.name ?? live.state.assets?.activeVoice ?? '默认'
  const voicePickLabel = view.voiceState.voiceId
    ? `我录的音色（${view.voiceState.voiceId}）`
    : view.voiceState.officialVoice
      ? view.voiceState.officialVoices.find(v => v.id === view.voiceState.officialVoice)?.label ?? view.voiceState.officialVoice
      : '默认音色'

  return <section className="work-page human-page cp-page">
    <div className="human-layout cp-layout">
      {/* 板头 = 主题锚点：大标题 + 一句话定位 + 当前人物/音色/连接（不再另设页面标题） */}
      <header className="cp-board-head">
        <div className="cp-board-title">
          <h1>情感数字人</h1>
          <p>实时口型 · 语音陪伴 · 把自己的照片变成数字人</p>
        </div>
        <div className="cp-board-meta">
          <span className="cp-meta-person">{avatarName || '未选择形象'}</span>
          <span className="cp-meta-voice">{voiceName}</span>
          <Badge status={connected ? 'success' : 'default'}
            text={connected ? '在线' : '连接中'} />
        </div>
      </header>
      <div className="cp-grid">
      {/* 左：会话配置条 + 陪伴对话 */}
      <section className="cp-panel cp-chat-panel">
        <CompanionPicker state={live.state} voiceOpen={voiceOpen}
          onToggleVoiceOpen={() => setVoiceOpen(open => !open)}
          onManage={() => setModal('management')}
          onSelectPreset={live.selectPreset}
          onAskDelete={() => {
            const current = live.state.currentAvatar || live.state.assets?.defaultAvatar || ''
            const preset = live.state.assets?.presets.find(item => item.avatar === current)
            if (!preset) { live.note('当前形象不在数字人列表里，无法删除'); return }
            if (['my_avatar', 'yy'].includes(preset.avatar)) { live.note(`「${preset.name}」是内置形象，不能删除`); return }
            if (window.confirm(`确定要删除数字人「${preset.name}」吗？素材删除后无法恢复，会自动切回默认形象。`)) {
              void live.deleteAvatar(preset.avatar, preset.name)
            }
          }}
          deleting={false}
          deleteTitle="删除当前数字人"
          onSwitchVoice={live.switchVoice} />
        {refreshError && <WorkError message={refreshError} />}
        {view.message && <WorkError message={view.message} />}
        <CompanionChat state={live.state}
          onSend={live.onSend} onToggleMic={live.onToggleMic} onInterrupt={live.onInterrupt}
          onRetry={live.onRetry} onAcceptDance={live.dance} onDismissInvite={live.clearRecommendation} />
      </section>

      {/* 中：实时口型舞台（页面主角，栏头让位给画面） */}
      <section className="cp-panel cp-stage-panel">
        <CompanionStage state={live.state}
          onRetry={live.onRetry} onTapPlay={live.onTapPlay} onVideoReady={live.onVideoReady} />
      </section>

      {/* 右：按需状态 + 使用说明 + 制作入口 */}
      <aside className="cp-side">
        <div className="cp-side-scroll">
          {live.state.connection === 'failed' && <section className="cp-panel">
            <header className="cp-side-head">连接异常</header>
            <div className="cp-status-rows">
              <div className="cp-status-row"><small>状态</small>
                <strong><Button size="small" className="cp-inline-retry" onClick={live.onRetry}>重新连接</Button></strong>
              </div>
            </div>
          </section>}

          {live.state.emotion && live.state.emotion.key !== 'neutral' && <section className="cp-panel">
            <header className="cp-side-head">情绪感知</header>
            <div className="cp-status-rows">
              <div className="cp-status-row"><small>当前情绪</small>
                <strong>{live.state.emotion.label}{live.state.emotion.consecutive > 1 ? ` ×${live.state.emotion.consecutive}` : ''}</strong>
              </div>
            </div>
          </section>}

        <section className="cp-panel">
          <header className="cp-side-head">使用指南</header>
          <CompanionAbility state={live.state} />
        </section>
        </div>

        <div className="cp-side-foot">
          <div className="cp-foot-actions">
            <Button block size="large" onClick={() => setModal('production')}>制作数字人 / 音色克隆</Button>
            <div className="cp-foot-row">
              <Button block onClick={() => setModal('key')}>模型 Key</Button>
              <Button block onClick={() => setModal('management')}>对象管理</Button>
            </div>
          </div>
          <p className="cp-disclaimer">AI 陪伴内容仅供参考，不能替代专业医生的建议。</p>
        </div>
      </aside>
      </div>
    </div>

    {modal === 'management' && <ManagementModal view={view} onClose={() => setModal('none')} />}
    {modal === 'production' && <ProductionModal view={view} onClose={() => setModal('none')} />}
    {modal === 'key' && <ModelKeyModal view={view} onClose={() => setModal('none')} />}
  </section>
}
