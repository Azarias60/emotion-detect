import type { DigitalHumanState } from '../domain/companionLive'

/** 状态胶囊文案（供外部按需复用）。 */
export function statusPill(state: DigitalHumanState): string {
  if (state.connection !== 'connected') return '连接中'
  if (state.speaking) return '说话中'
  if (state.busy) return '思考中'
  if (state.listening) return '聆听中'
  return '待机'
}

/** 字幕条文案：仅在刚说完时展示回复（截到 28 字）。 */
export function captionText(state: DigitalHumanState): string {
  const last = state.messages[state.messages.length - 1]
  if (state.captionFresh && last && last.role === 'assistant' && last.content) {
    const text = last.content.trim().replace(/^\[chat\]\s*/, '')
    return text.length > 28 ? text.slice(0, 28) + '…' : text
  }
  return ''
}

export interface DigitalHumanStageProps {
  state: DigitalHumanState
  onRetry: () => void
  onTapPlay: () => void
  onVideoReady: (video: HTMLVideoElement) => void
}

/**
 * 陪伴页中央舞台：WebRTC 实时口型画面（页面主角）+ 仅在说话时浮现的字幕条。
 * 纯展示组件——会话状态与动作全部由上层传入（分层约定）。
 */
export function DigitalHumanStage({ state, onRetry, onTapPlay, onVideoReady }: DigitalHumanStageProps) {
  const connected = state.connection === 'connected'
  const failed = state.connection === 'failed'
  const caption = connected ? captionText(state) : ''
  return <div className="cp-stage-wrap">
    <div className={`cp-hologram${state.speaking ? ' is-speaking' : ''}`}>
      {/* 注意：不能 muted——WebRTC 语音从这条 video 出来 */}
      <video data-digital-human-video playsInline autoPlay
        ref={(element) => { if (element) onVideoReady(element) }}
        className="cp-video" aria-label="数字人实时画面" />
      {state.needPlayTap && <button type="button" className="cp-play-tap" onClick={onTapPlay}>
        <span aria-hidden="true" />点击开播
      </button>}
      {state.switching && connected && <div className="cp-stage-mask">
        <span className="cp-spinner" aria-hidden="true" />
        <p>{state.connectionDetail}</p>
      </div>}
      {!connected && <div className="cp-stage-mask">
        {!failed && <span className="cp-spinner" aria-hidden="true" />}
        <p>{state.connectionDetail || '正在连接数字人…'}</p>
        {failed && <button type="button" className="cp-retry" onClick={onRetry}>重试连接</button>}
      </div>}
      {connected && state.listening && state.diag.audioBlocked && <div className="cp-state-line">
        <small className="is-warn">请点击页面恢复声音</small>
      </div>}
      {caption && <p className="cp-caption is-fresh">{caption}</p>}
    </div>
  </div>
}
