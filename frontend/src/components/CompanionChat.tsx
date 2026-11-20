import { useEffect, useRef, useState } from 'react'
import { ArrowDown, Check, Copy, Mic, MicOff, Send, Square } from 'lucide-react'
import type { ChatMessage, DigitalHumanState } from '../domain/companionLive'


/** 等待回复的打字指示器（市场惯例：三点跳动 = "对方正在输入"）。 */
function TypingDots({ label }: { label: string }) {
  return <div className="dh-msg dh-msg--assistant typing-row" aria-live="polite">
    <span className="dh-msg-role">TA</span>
    <span className="typing-dots" title={label}>
      <i /><i /><i />
    </span>
  </div>
}

/** 助手回复悬浮复制按钮：点击写入剪贴板并短暂显示"已复制"。 */
function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return <button type="button" className="copy-btn" aria-label="复制这条回复"
    onClick={() => {
      void navigator.clipboard?.writeText(text).then(() => {
        setCopied(true)
        setTimeout(() => setCopied(false), 1500)
      }).catch(() => {})
    }}>
    {copied ? <Check size={13} /> : <Copy size={13} />}
    <em>{copied ? '已复制' : '复制'}</em>
  </button>
}

function Message({ message, onRetry, onMic }: {
  message: ChatMessage
  onRetry: () => void
  onMic: () => void
}) {
  if (message.role === 'action') {
    return <p className="dh-msg dh-msg--note">{message.content}</p>
  }
  if (message.role === 'error') {
    return <div className="dh-msg dh-msg--error">
      <span>{message.content}</span>
      <button type="button" onClick={message.retry === 'mic' ? onMic : onRetry}>
        {message.retry === 'mic' ? '去开麦克风' : '重试'}
      </button>
    </div>
  }
  return <div className={`dh-msg dh-msg--${message.role}`}>
    <span className="dh-msg-role">{message.role === 'assistant' ? 'TA' : '我'}</span>
    <p>{message.content}</p>
    {message.role === 'assistant' && message.content && <CopyButton text={message.content} />}
  </div>
}

export interface CompanionChatProps {
  state: DigitalHumanState
  onSend: (text: string) => void
  onToggleMic: () => void
  onInterrupt: () => void
  onRetry: () => void
  /** 接受舞蹈邀请（引擎连续同情绪时发出） */
  onAcceptDance: () => void
  /** 下次吧：只收邀请，不动数字人 */
  onDismissInvite: () => void
}

/**
 * 陪伴页左栏：实时对话流 + 舞蹈邀请 + 输入行（麦克风电平条 / 发送 / 打断）。
 * 消息流限宽居中：行长 60~70 字最易读，宽屏不会一条消息横跨整个面板。
 */
export function CompanionChat({ state, onSend, onToggleMic, onInterrupt, onRetry, onAcceptDance, onDismissInvite }: CompanionChatProps) {
  const [draft, setDraft] = useState('')
  const streamRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const [showJump, setShowJump] = useState(false)
  const connected = state.connection === 'connected'
  const waiting = state.busy && !state.speaking
  const busy = state.busy || state.speaking
  const invite = state.recommendation?.kind === 'dance' ? state.recommendation : null
  const lastMsg = state.messages[state.messages.length - 1]

  useEffect(() => {
    const el = streamRef.current
    if (el) el.scrollTop = el.scrollHeight
    setShowJump(false)
  }, [state.messages.length, state.messages[state.messages.length - 1]?.content])

  // 进入页面/连上后聚焦输入框（键盘直接可打字）
  useEffect(() => {
    if (connected) inputRef.current?.focus()
  }, [connected])

  // 用户上翻离开底部时，新消息到达改为显示"回到底部"
  function onScroll() {
    const el = streamRef.current
    if (!el) return
    setShowJump(el.scrollHeight - el.scrollTop - el.clientHeight > 150)
  }

  const submit = () => {
    const text = draft.trim()
    if (!text) return
    onSend(text)
    setDraft('')
  }

  return <div className="cp-chat">
    <div className="cp-chat-head">
      <span className="cp-panel-label">实时对话</span>
      {state.emotion && state.emotion.key !== 'neutral' && <span
        className={`cp-emo cp-emo--${state.emotion.key}`}
        title={state.emotion.hint}>
        {state.emotion.label}{state.emotion.consecutive > 1 && <i>×{state.emotion.consecutive}</i>}
      </span>}
    </div>

    <div className={`dh-chat cp-chat-stream${state.messages.length === 0 && !invite ? ' is-empty' : ''}`}
      ref={streamRef} onScroll={onScroll} role="log" aria-live="polite">
      {state.messages.length === 0
        ? <div className="cp-starter-block">
            <span className="cp-starter-orb" aria-hidden="true"><Mic size={22} /></span>
            <p className="cp-starter-note">有什么想聊的，直接开口，或点下面的问法。</p>
            <div className="cp-starters" aria-label="试试这样问">
              {['你好', '我最近总失眠', '陪我聊聊天'].map((ask) => (
                <button key={ask} type="button" className="gd-suggest-chip" disabled={!connected}
                  onClick={() => onSend(ask)}>{ask}</button>
              ))}
            </div>
          </div>
        : state.messages.map((message) =>
            <Message key={message.id} message={message} onRetry={onRetry} onMic={onToggleMic} />)}
      {waiting && lastMsg?.role === 'user' && <TypingDots label="正在思考…" />}

      {showJump && <button type="button" className="jump-bottom" aria-label="回到底部"
        onClick={() => { const el = streamRef.current; if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' }) }}>
        <ArrowDown size={14} />
      </button>}

      {invite && <div className="dh-msg cp-dance-invite">
        <span className="dh-msg-role">TA</span>
        <div className="cp-dance-body">
          <p>{invite.reason || '要不要看段舞放松一下？'}</p>
          <div className="cp-dance-actions">
            <button type="button" className="cp-action-btn" disabled={state.dancing} onClick={onAcceptDance}>
              {state.dancing ? '准备中…' : '好，跳一段'}
            </button>
            <button type="button" className="cp-ghost-btn" onClick={onDismissInvite}>下次吧</button>
          </div>
        </div>
      </div>}
    </div>

    <div className="cp-input-row">
      <button type="button" className={`cp-mic${state.listening ? ' is-on' : ''}`}
        onClick={onToggleMic} disabled={!connected}
        aria-pressed={state.listening}
        title={state.listening ? `正在录音（音量 ${Math.round(state.diag.micLevel * 100)}%）· 点击关闭` : '开启麦克风'}
        aria-label={state.listening ? '关闭麦克风' : '打开麦克风'}>
        {state.listening ? <Mic size={17} /> : <MicOff size={17} />}
        {state.listening && <span className="cp-mic-meter" aria-hidden="true">
          <i style={{ width: `${Math.round(state.diag.micLevel * 100)}%` }} />
        </span>}
      </button>
      <input ref={inputRef} value={draft} onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => { if (event.key === 'Enter' && !event.nativeEvent.isComposing) submit() }}
        placeholder={connected ? '跟数字人说点什么…' : '等待数字人就绪…'}
        disabled={!connected} aria-label="对话输入" />
      {busy
        ? <button type="button" className="cp-send is-stop" title="停止生成" onClick={onInterrupt} aria-label="停止生成">
            <Square size={15} />
          </button>
        : <button type="button" className="cp-send" onClick={submit}
            disabled={!draft.trim() || !connected} aria-label="发送">
            <Send size={16} />
          </button>}
    </div>
  </div>
}
