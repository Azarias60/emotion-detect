import { useEffect, useRef, useState, type ComponentProps } from 'react'
import { Activity, ArrowRight, ArrowDown, Check, Copy, Mic, MicOff, Send, Smile, Square } from 'lucide-react'
import type { GuideBinding } from '../domain/guideSession'
import type { HeardEntry } from '../domain/guideSession'
import { GuideAvatar } from './GuideAvatar'
import { GuideDocCard } from './GuideDocCard'

/** 推荐胶囊用中文模块名（引擎下发的是内部键 health/fatigue/emotion/companion）。 */
const MODULE_CN: Record<string, string> = {
  health: '健康检测', fatigue: '疲劳检测', emotion: '情绪检测', companion: '情感数字人',
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

/** 等待回复的打字指示器（三点跳动 = "对方正在输入"）。 */
function TypingDots() {
  return <div className="gd-msg gd-msg--assistant typing-row" aria-live="polite">
    <span className="gd-msg-role">暖暖</span>
    <span className="typing-dots" title="正在思考…">
      <i /><i /><i />
    </span>
  </div>
}

const ROLE_LABEL: Record<HeardEntry['role'], string> = {
  user: '我',
  assistant: '暖暖',
  system: '',
}

/** 首屏快捷问法：给空对话一个可点的开始 */
const QUICK_ASKS = ['健康检测', '报告怎么看', '我有点累']

const MODULES = [
  { title: '健康检测', desc: '舌象 · 心率', href: '/detection?mode=health', icon: Activity },
  { title: '疲劳检测', desc: '眨眼 · 哈欠', href: '/detection?mode=fatigue', icon: RunningIcon },
  { title: '情绪检测', desc: '表情 · 语音', href: '/detection?mode=emotion', icon: Smile },
  { title: '情感数字人', desc: '口型 · 陪伴', href: '/digital-human', icon: DigitalHumanIcon },
] as const

function RunningIcon(props: ComponentProps<'svg'>) {
  return (
    <svg {...props} viewBox="0 0 64 64" fill="none" aria-hidden="true">
      <circle cx="42" cy="9" r="6" fill="currentColor" stroke="none" />
      <g stroke="currentColor" strokeWidth="3.6" strokeLinecap="round" strokeLinejoin="round">
        <path d="m14 24 13-4 11 7 10 1 9-7" />
        <path d="m31 23-10 16 15 4-9 15M22 38 13 51 6 57" />
      </g>
    </svg>
  )
}

function DigitalHumanIcon(props: ComponentProps<'svg'>) {
  return (
    <svg {...props} viewBox="0 0 64 72" fill="none" aria-hidden="true">
      <defs>
        <linearGradient id="head-volume" x1="17" y1="9" x2="49" y2="64" gradientUnits="userSpaceOnUse">
          <stop stopColor="#cfe9ff" stopOpacity=".8" />
          <stop offset=".48" stopColor="#2f7fd0" stopOpacity=".34" />
          <stop offset="1" stopColor="#8fc4ff" stopOpacity=".1" />
        </linearGradient>
        <clipPath id="head-contour">
          <path d="M12 68 23 54V45C11 39 10 28 12 19 14 9 22 5 32 5c13 0 20 9 20 21l5 10-6 3v8c-1 6-8 7-14 7l-1 9 12 5" />
        </clipPath>
      </defs>
      <path d="M12 68 23 54V45C11 39 10 28 12 19 14 9 22 5 32 5c13 0 20 9 20 21l5 10-6 3v8c-1 6-8 7-14 7l-1 9 12 5" fill="url(#head-volume)" stroke="currentColor" strokeWidth="2.1" />
      <g clipPath="url(#head-contour)" stroke="currentColor" strokeWidth="1" opacity=".4">
        <path d="m5 13 50 3M5 21l50 4M5 29l50 4M5 37l50 4M5 45l50 4M5 53l50 4M5 61l50 4" />
        <path d="m18 4 7 63M27 4l4 63M36 4l1 63M45 4l-3 63M8 23l17-11 18 10-15 15 23 10M14 40l14-3-5 17 14 9" />
      </g>
      <circle cx="45" cy="28" r="1" fill="#eef6ff" />
      <path d="m47 44 5-1M25 29c-6-7-11 3-4 7" stroke="currentColor" strokeWidth="2.1" />
    </svg>
  )
}

function Message({ entry }: { entry: HeardEntry }) {
  if (entry.role === 'system') {
    return <p className={`gd-msg gd-msg--note${entry.error ? ' gd-msg--error' : ''}`}>{entry.text}</p>
  }
  return <div className={`gd-msg gd-msg--${entry.role}`}>
    <span className="gd-msg-role">{ROLE_LABEL[entry.role]}</span>
    <p>{entry.text}</p>
    {entry.role === 'assistant' && entry.text && <CopyButton text={entry.text} />}
  </div>
}

/**
 * 首页控制台：左侧功能模块 / 中间暖暖舞台 / 右侧导览对话，一块整板三栏。
 * 状态与动作全部来自 useGuide 的绑定，组件只负责排版。
 */
export function GuideBoard({ guide }: { guide: GuideBinding }) {
  const { state } = guide
  const [draft, setDraft] = useState('')
  const logRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const [showJump, setShowJump] = useState(false)
  const connected = state.connection === 'connected'
  const waiting = state.busy && !state.speaking
  const busy = state.busy || state.speaking
  const stateLabel = state.speaking
    ? '正在说话…'
    : state.busy
      ? '思考中…'
      : state.listening
        ? '在听…'
        : connected ? '待机' : state.connectionDetail || '连接中'
  const lastMsg = state.heard[state.heard.length - 1]

  useEffect(() => {
    const el = logRef.current
    if (el) el.scrollTop = el.scrollHeight
    setShowJump(false)
  }, [state.heard.length, state.speaking])

  useEffect(() => {
    if (connected) inputRef.current?.focus()
  }, [connected])

  function onScroll() {
    const el = logRef.current
    if (!el) return
    setShowJump(el.scrollHeight - el.scrollTop - el.clientHeight > 150)
  }

  const submit = () => {
    const text = draft.trim()
    if (!text) return
    guide.sendText(text)
    setDraft('')
  }

  return <div className="board">
    <div className="board-console">
    {/* 板头 = 主题锚点（标题与一句话定位进控制台，人物随之变高变大） */}
    <header className="board-head">
      <h2 className="board-title">未来健康控制中心</h2>
      <p className="board-sub">语音导览 · 智能检测 · 一步直达</p>
    </header>
    <div className="board-grid">
      <nav className="board-rail" aria-label="功能模块">
        {MODULES.map(({ title, desc, href, icon: Icon }, index) => (
          <a key={title} className="module board-module" data-glass={`module-${index + 1}`} href={href}>
            <span className="board-module-icon"><Icon strokeWidth={1.5} aria-hidden="true" /></span>
            <span className="board-module-text">
              <span className="board-module-title">{title}</span>
              <span className="board-module-desc">{desc}</span>
            </span>
            <ArrowRight className="board-module-arrow" size={16} strokeWidth={1.6} aria-hidden="true" />
          </a>
        ))}
      </nav>

      <section className="board-stage" aria-label="导览员暖暖">
        <span className="board-halo" aria-hidden="true" />
        <GuideAvatar speaking={state.speaking} />
        <p className="board-name">暖暖<span>导览员</span></p>
        {!connected && <span className="board-state">{stateLabel}</span>}
      </section>

      <section className="board-chat" aria-label="导览对话">
        <p className="board-label">导览对话</p>
        <div className={`guide-log${state.heard.length === 0 ? ' is-empty' : ''}`} ref={logRef} onScroll={onScroll} role="log" aria-live="polite">
          {state.heard.length === 0
            ? <>
                <p className="gd-msg gd-msg--note">
                  {connected
                    ? '我是暖暖。说「你好」开始语音对话，也可以点下面的问法。'
                    : state.connectionDetail || '暖暖正在过来，马上就好…'}
                </p>
                <div className="gd-suggest">
                  {QUICK_ASKS.map((ask) => (
                    <button key={ask} type="button" className="gd-suggest-chip" disabled={!connected}
                      onClick={() => guide.sendText(ask)}>{ask}</button>
                  ))}
                </div>
              </>
            : state.heard.map((entry) => <Message key={entry.id} entry={entry} />)}
          {waiting && lastMsg?.role === 'user' && <TypingDots />}
          {showJump && <button type="button" className="jump-bottom" aria-label="回到底部"
            onClick={() => { const el = logRef.current; if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' }) }}>
            <ArrowDown size={14} />
          </button>}
        </div>

        {state.recommend && <p className="gd-recommend">
          <span className="gd-recommend-chip">推荐 · {MODULE_CN[state.recommend.module] ?? state.recommend.module}</span>
          <span className="gd-recommend-note">
            {state.recommend.reason} 觉得可以的话，说「好」我就带你过去。
          </span>
        </p>}

        <GuideDocCard doc={state.doc} />

        <div className="guide-input-row">
          <button type="button" className={`guide-mic${state.listening ? ' is-on' : ''}`}
            onClick={guide.toggleMic} disabled={state.connection === 'failed'}
            aria-pressed={state.listening}
            aria-label={state.listening ? '关闭麦克风' : '打开麦克风'}>
            {state.listening ? <Mic size={16} /> : <MicOff size={16} />}
          </button>
          <input ref={inputRef} value={draft} onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => { if (event.key === 'Enter' && !event.nativeEvent.isComposing) submit() }}
            placeholder={connected ? '和暖暖聊聊吧…' : '等待暖暖就绪…'}
            disabled={state.connection === 'failed'} aria-label="向暖暖提问" />
          {busy
            ? <button type="button" className="guide-stop" title="停止生成" onClick={guide.interrupt} aria-label="停止生成">
                <Square size={14} />
              </button>
            : <button type="button" onClick={submit} disabled={!connected || !draft.trim()} aria-label="发送">
                <Send size={15} />
              </button>}
        </div>
      </section>
    </div>
    </div>
  </div>
}
