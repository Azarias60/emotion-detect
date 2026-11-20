import { Trash2, Volume2 } from 'lucide-react'
import type { DigitalHumanState } from '../domain/companionLive'

/** 左栏标题用的当前数字人名：选了预设跟预设走，否则回退默认。 */
export function stageNameOf(state: DigitalHumanState): string {
  const current = state.currentAvatar || state.assets?.defaultAvatar || ''
  const active = state.assets?.presets.find((p) => p.avatar === current && p.ready)
  return active?.name || '数字人'
}

export interface CompanionPickerProps {
  state: DigitalHumanState
  /** 手动音色面板是否展开（唯一破坏"人物↔音色"配对的入口） */
  voiceOpen: boolean
  onToggleVoiceOpen: () => void
  /** 打开对象管理弹窗（配置/形象/音色/素材） */
  onManage: () => void
  onSelectPreset: (id: string) => void
  onAskDelete: () => void
  deleting: boolean
  deleteTitle: string
  onSwitchVoice: (id: string) => void
}

/**
 * 数字人选择条：预设下拉一项 = 形象 + 音色成对切换；
 * 删除是次要操作靠右；「音色」按钮展开手动覆盖面板（列表由后台自动静默刷新）。
 */
export function CompanionPicker({ state, voiceOpen, onToggleVoiceOpen, onManage, onSelectPreset, onAskDelete, deleting, deleteTitle, onSwitchVoice }: CompanionPickerProps) {
  const presets = state.assets?.presets ?? []
  if (!presets.length) return null
  const current = state.currentAvatar || state.assets?.defaultAvatar || ''
  // 严格配对（形象+音色都对上）→ 只有形象对上也算"选了这个人"，避免下拉框
  // 因为音色不一致就显示成空（用户看来是"没选中任何数字人"）
  const activeId = presets.find((p) => p.avatar === current && p.voice === state.assets?.activeVoice)?.id
    ?? presets.find((p) => p.avatar === current && p.ready)?.id
    ?? ''
  const active = presets.find((p) => p.id === activeId) ?? null
  const canDelete = Boolean(active && !['my_avatar', 'yy'].includes(active.avatar))
  const mixHint = !activeId
    ? `当前为混搭（${current || '--'} + ${state.assets?.activeVoice || '--'}），可重新选择配对`
    : ''
  void onManage

  return <div className="cp-picker">
    <div className="cp-picker-head">
      <span className="cp-panel-label">形象与音色</span>
      {canDelete && <div className="cp-picker-actions">
        <button type="button" className="cp-icon-btn is-danger" disabled={deleting}
          title={deleteTitle} aria-label={deleteTitle} onClick={onAskDelete}>
          <Trash2 size={13} aria-hidden="true" />
        </button>
      </div>}
    </div>
    <div className="cp-picker-row">
      <select className="cp-select" value={activeId}
        title={active?.note || '选择数字人'}
        onChange={(event) => onSelectPreset(event.target.value)}
        aria-label="选择数字人">
        {!activeId && <option value="" disabled>当前为混搭，请选择</option>}
        {presets.map((preset) => (
          <option key={preset.id} value={preset.id} disabled={!preset.ready}>
            {preset.name}{preset.ready ? '' : '（未就绪）'}
          </option>
        ))}
      </select>
      <button type="button" className={`cp-mini-btn cp-voice-btn${voiceOpen ? ' is-on' : ''}`}
        title="切换音色" onClick={onToggleVoiceOpen}>
        <Volume2 size={13} aria-hidden="true" />音色
      </button>
    </div>
    {voiceOpen && <div className="cp-voice-panel">
      <p className="cp-voice-tip">默认随形象自动配对，也可手动指定音色：</p>
      <div className="cp-voice-list">
        {(state.assets?.voices ?? []).map((voice) => (
          <button key={voice.id} type="button" className="cp-voice-item"
            data-active={voice.id === state.assets?.activeVoice || undefined}
            onClick={() => onSwitchVoice(voice.id)}>
            <span>{voice.name}</span>
            {voice.id === state.assets?.activeVoice && <em>✓</em>}
          </button>
        ))}
      </div>
    </div>}
    {mixHint && <p className="cp-picker-note is-warn">{mixHint}</p>}
  </div>
}
