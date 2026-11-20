import { useRef, useState } from 'react'
import type { DigitalHumanState } from '../domain/companionLive'
import { stageLabel as studioStageLabel, type StudioState } from '../domain/avatarStudio'
import type { VoiceCloneState } from '../domain/voiceClone'
import { statusPill } from './CompanionStage'

/** 当前音色名（引擎表）：配对中点名是哪个预设的音色，混搭时显示音色本身。 */
export function voiceLabelOf(state: DigitalHumanState): string {
  const assets = state.assets
  if (!assets) return '--'
  const current = state.currentAvatar || assets.defaultAvatar || ''
  const paired = assets.presets.find((p) => p.avatar === current && p.ready)
  if (paired) {
    const preset = assets.presets.find((p) => p.id === paired.id)
    if (preset) return assets.voices.find((v) => v.id === preset.voice)?.name ?? preset.voice
  }
  return assets.voices.find((v) => v.id === assets.activeVoice)?.name ?? assets.activeVoice ?? '--'
}

/** 当前数字人：名字与音色分两行（窄栏里硬折行会打乱标签-值节奏）。 */
export function currentPersonaOf(state: DigitalHumanState): { name: string; voice: string } {
  const assets = state.assets
  if (!assets) return { name: '--', voice: '' }
  const current = state.currentAvatar || assets.defaultAvatar || '--'
  const voiceName = assets.voices.find((v) => v.id === assets.activeVoice)?.name ?? assets.activeVoice ?? '--'
  const paired = assets.presets.find((p) => p.avatar === current && p.ready)
  if (paired && assets.activeVoice === paired.voice) {
    return { name: paired.name, voice: voiceName }
  }
  return { name: `混搭：${current}`, voice: voiceName }
}

export function CompanionStatus({ state, onRetry }: {
  state: DigitalHumanState
  onRetry: () => void
}) {
  const { diag } = state
  // 上行诊断：「音量在跳但没识别」时一眼定位断点——音量条只证明麦克风有输入
  const kb = Math.round(diag.uplinkBytes / 1024)
  const uplinkDiag = !state.listening ? ''
    : !diag.uplinkBytes ? '未发送（通道未就绪）'
    : !diag.engineEvents ? `已发 ${kb}KB · 引擎无回传`
    : `已发 ${kb}KB · 引擎回传 ${diag.engineEvents} 条`
  const emotion = state.emotion && state.emotion.key !== 'neutral' ? state.emotion : null

  return <div className="cp-status-rows">
    <div className="cp-status-row">
      <small>连接状态</small>
      <strong>
        {state.connection === 'connected' ? '实时数字人已连接'
          : state.connection === 'failed' ? <>连接失败 <button type="button" className="cp-inline-retry" onClick={onRetry}>重试</button></>
          : state.connection === 'connecting' ? '连接中' : '未连接'}
      </strong>
    </div>
    <div className="cp-status-row"><small>当前数字人</small><strong className="cp-persona">
      <span>{currentPersonaOf(state).name}</span>
      {currentPersonaOf(state).voice && <i>{currentPersonaOf(state).voice}</i>}
    </strong></div>
    <div className="cp-status-row"><small>当前状态</small><strong>{statusPill(state)}</strong></div>
    {state.listening && <div className="cp-status-row">
      <small>麦克风音量</small>
      <strong className={diag.audioBlocked || state.micSilent ? 'is-warn' : ''}>
        {Math.round(diag.micLevel * 100)}%
        {diag.audioBlocked && <i className="cp-status-tip"> · 浏览器挂起了采音，点页面任意处恢复</i>}
        {!diag.audioBlocked && state.micSilent && <i className="cp-status-tip"> · 没有收到声音</i>}
      </strong>
    </div>}
    {state.listening && uplinkDiag && <div className="cp-status-row">
      <small>语音上行</small><strong className="cp-diag">{uplinkDiag}</strong>
    </div>}
    <div className="cp-status-row">
      <small>对话轮次</small>
      <strong>{state.messages.filter((message) => message.role === 'user').length}</strong>
    </div>
    {emotion && <div className="cp-status-row">
      <small>当前情绪</small>
      <strong className={`cp-emo-text cp-emo-text--${emotion.key}`}>
        {emotion.label}{emotion.consecutive > 1 && <i> ×{emotion.consecutive}</i>}
      </strong>
    </div>}
  </div>
}

/** 使用指南：精简三行自然中文，不露内部术语。 */
export function CompanionAbility({ state }: { state: DigitalHumanState }) {
  return <div className="cp-ability">
    <div><b>语音对话</b><small>说「你好」即可唤醒，说完自动发送</small></div>
    <div><b>当前音色</b><small>{voiceLabelOf(state)}</small></div>
    <div><b>实时互动</b><small>支持口型同步与自然表情回应</small></div>
  </div>
}

/** drop 视频生成行：素材工场（LiveTalking 投递 → 一键生成新形象）。 */
function dropRowsOf(state: DigitalHumanState) {
  const assets = state.assets
  if (!assets) return []
  const jobs = new Map(assets.generations.map((job) => [job.name, job]))
  return assets.dropVideos
    .filter((item) => !(item.built && !jobs.has(item.name))) // 已生成且无进行中任务的不占位
    .map((item) => {
      const job = jobs.get(item.name)
      let statusText = '待生成'
      if (item.generating || job?.status === 'running') statusText = '生成中…'
      else if (job?.status === 'error') statusText = job.message || '生成失败'
      return { name: item.name, file: item.file, built: item.built, statusText }
    })
}

export interface CompanionStudioProps {
  state: DigitalHumanState
  voice: VoiceCloneState
  studio: StudioState
  /** 上传/抓取/创建/任务进行中：制作入口整体禁用 */
  studioBusy: boolean
  onPickPhoto: () => void
  onFetchUrl: (url: string) => void
  onConfirmCreate: (name: string) => void
  onCancelJob: () => void
  cancelling: boolean
  onDismissStudio: () => void
  onGenerate: (name: string) => void
  onDance: () => void
  canDance: boolean
  onRecordVoice: () => void
  onStopRecord: () => void
  onPickSentence: () => void
  onPickOfficial: (voiceId: string) => void
  onBindVoice: (avatarId: string) => void
}

/**
 * 制作数字人面板（默认收起）：素材工场 + 上传照片/直链 → 起名 → 产线进度 →
 * 语音克隆（引导句录音 / 官方备用音色 / 绑定）+ 舞蹈段。
 */
export function CompanionStudio({
  state, voice, studio, studioBusy, onPickPhoto, onFetchUrl, onConfirmCreate, onCancelJob,
  cancelling, onDismissStudio, onGenerate, onDance, canDance,
  onRecordVoice, onStopRecord, onPickSentence, onPickOfficial, onBindVoice,
}: CompanionStudioProps) {
  const rows = dropRowsOf(state)
  const running = studio.status === 'running'
  const progress = running && studio.total > 0 && studio.stageIndex >= 0
    ? Math.round(((studio.stageIndex + 1) / studio.total) * 100)
    : 0
  const voicePickLabel = voice.voiceId
    ? `我录的音色（${voice.voiceId}）`
    : voice.officialVoice
      ? voice.officialVoices.find((v) => v.id === voice.officialVoice)?.label ?? voice.officialVoice
      : '未选（制作完成后使用默认音色）'
  const presets = state.assets?.presets ?? []
  // 面板内的两块临时草稿：不进全局状态树，提交时才读值交给上层
  const urlInputRef = useRef<HTMLInputElement>(null)
  const nameInputRef = useRef<HTMLInputElement>(null)
  const [bindTarget, setBindTarget] = useState('')

  return <div className="cp-studio">
    {rows.length > 0 && rows.map((row) => (
      <div key={row.name} className="cp-asset-row">
        <span className="cp-asset-name" title={row.file}>{row.file}</span>
        <span className={`cp-asset-status${row.statusText === '生成中…' ? ' is-running' : row.statusText.startsWith('生成失败') ? ' is-error' : ''}`}>
          {row.built && row.statusText === '待生成' ? '已生成' : row.statusText}
        </span>
        <button type="button" className="cp-action-btn" disabled={row.statusText === '生成中…'}
          onClick={() => onGenerate(row.name)}>
          {row.built ? '重新生成' : '生成人物'}
        </button>
      </div>
    ))}
    {rows.length === 0 && <p className="cp-studio-hint">暂无待生成的素材，可直接上传照片制作</p>}

    {studio.interruptedNotice && <p className="cp-studio-notice">{studio.interruptedNotice}</p>}

    <div className="cp-studio-block">
      <button type="button" className="cp-studio-entry" disabled={studioBusy} onClick={onPickPhoto}>
        ＋ 上传照片制作数字人
      </button>
      <div className="cp-studio-url">
        <input ref={urlInputRef} placeholder="或粘贴图片直链（http/https）" disabled={studioBusy}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.nativeEvent.isComposing) onFetchUrl(event.currentTarget.value)
          }} />
        <button type="button" className="cp-action-btn" disabled={studioBusy}
          onClick={() => onFetchUrl(urlInputRef.current?.value ?? '')}>抓取</button>
      </div>
      {studio.jobId && !running && <div className="cp-studio-name">
        <input ref={nameInputRef} maxLength={10} placeholder="给数字人起个名字（可留空）" disabled={studio.creating}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.nativeEvent.isComposing) onConfirmCreate(event.currentTarget.value)
          }} />
        <button type="button" className="cp-action-btn" disabled={studio.creating}
          onClick={() => onConfirmCreate(nameInputRef.current?.value ?? '')}>开始制作</button>
      </div>}
      {studio.uploading && <p className="cp-studio-progress">正在获取照片…</p>}
      {running && <div className="cp-studio-progress">
        <div className="cp-studio-bar"><i style={{ width: `${progress}%` }} /></div>
        <small>{studioStageLabel(studio.stage)}（{studio.stageIndex + 1}/{studio.total}）{studio.message ? `—— ${studio.message}` : ''}</small>
        <button type="button" className="cp-action-btn is-cancel" disabled={cancelling} onClick={onCancelJob}>
          {cancelling ? '取消中…' : '取消制作'}
        </button>
      </div>}
      {studio.status === 'completed' && <div className="cp-studio-progress is-done">
        制作完成！数字人列表已自动刷新，在「数字人」里就能选到新数字人。
        <button type="button" className="cp-action-btn" onClick={onDismissStudio}>知道了</button>
      </div>}
      {studio.status === 'interrupted' && <div className="cp-studio-progress is-error">
        制作任务已中断（后台进程不在了，可能是后端重启或被手动结束）。请重新上传照片开始。
        <button type="button" className="cp-action-btn" onClick={onDismissStudio}>知道了</button>
      </div>}
      {(studio.status === 'failed' || studio.error) && studio.status !== 'interrupted'
        && <div className="cp-studio-progress is-error">
          {studio.error || studio.message}
          <button type="button" className="cp-action-btn" onClick={onDismissStudio}>关闭</button>
        </div>}

      {/* 语音克隆：读一句引导句当素材，与照片数字人配合（零样本克隆） */}
      <div className="cp-clone">
        <div className="cp-clone-head">🎙 为即将制作的数字人选音色</div>
        <p className="cp-clone-hint">
          录 3~15 秒自己的声音，或选官方备用音色；制作完成时自动绑给新数字人。都不选则用默认音色。
        </p>
        <p className="cp-clone-current">当前选择：<b>{voicePickLabel}</b></p>
        {voice.sentence && <div className="cp-clone-sentence">
          「{voice.sentence}」
          <button type="button" className="cp-action-btn"
            disabled={voice.recording || voice.uploading} onClick={onPickSentence}>换一句</button>
        </div>}
        <div className="cp-clone-actions">
          {!voice.recording
            ? <button type="button" className="cp-action-btn" disabled={voice.uploading} onClick={onRecordVoice}>
                {voice.uploading ? '上传中…' : voice.voiceId ? '重新录音' : '开始录音'}
              </button>
            : <button type="button" className="cp-action-btn is-cancel" onClick={onStopRecord}>
                停止并上传（{voice.seconds}s）
              </button>}
        </div>
        <div className="cp-clone-official">
          <span>不想录音？官方备用音色：</span>
          {voice.officialVoices.filter((v) => v.available !== false).map((v) => (
            <button key={v.id} type="button"
              className={`cp-action-btn${voice.officialVoice === v.id ? ' is-active' : ''}`}
              disabled={voice.recording || voice.uploading}
              onClick={() => onPickOfficial(v.id)}>{v.label}</button>
          ))}
        </div>
        {voice.error && <p className="cp-studio-progress is-error">{voice.error}</p>}
        {voice.done && <p className="cp-studio-progress is-done">{voice.done}</p>}
        {voice.voiceId && !voice.boundAvatar && <div className="cp-clone-bind">
          <select value={bindTarget} aria-label="选择要绑定的数字人"
            onChange={(event) => setBindTarget(event.target.value)}>
            <option value="" disabled>选择要绑定的数字人</option>
            {presets.filter((p) => p.ready).map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
          <button type="button" className="cp-action-btn" disabled={!bindTarget}
            onClick={() => onBindVoice(bindTarget)}>绑定音色</button>
        </div>}
      </div>
    </div>

    {canDance && <div className="cp-asset-row">
      <span className="cp-asset-name">舞蹈段</span>
      <span className="cp-asset-status">当前形象已内置</span>
      <button type="button" className="cp-action-btn"
        disabled={!state.connection || state.connection !== 'connected' || state.dancing}
        onClick={onDance}>
        {state.dancing ? '准备中…' : '跳一段舞'}
      </button>
    </div>}
  </div>
}
