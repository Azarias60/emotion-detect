import { useEffect, useRef, useState, type MouseEventHandler, type ReactNode } from 'react'
import { Dropdown } from 'antd'
import { DownOutlined } from '@ant-design/icons'
import { Activity, UserRound } from 'lucide-react'
import { GlassSurface } from '../components/GlassPanel'
import { Workspace } from '../components/WorkSurface'
import { modes, type DetectionMode } from '../domain/detection'
import { navigation, appRouteFor, isDetectionPath, detectionHref, ACCOUNT_PATH, DETECTION_PATH, LOGIN_PATH } from './routes'

type NavigationProps = {
  path: string
  userName: string | null
  mode: DetectionMode
  navigate: (href: string) => void
}

function Topbar({ path, userName, mode, navigate }: NavigationProps) {
  const [modeMenuOpen, setModeMenuOpen] = useState(false)
  const detecting = isDetectionPath(path)
  return (
    <header className="topbar">
      <GlassSurface kind="nav">
        <div className="capsule-shell">
          <a className="brand" href="/" aria-label="PhysioSense 首页">
            <span className="brand-mark"><Activity size={24} strokeWidth={1.75} /></span>
            <span>PhysioSense</span>
          </a>
          <nav className="navigation" aria-label="主导航">
        {navigation.map(item => item.path === DETECTION_PATH
          ? <Dropdown key={item.path} trigger={['click']} autoFocus open={modeMenuOpen} onOpenChange={setModeMenuOpen}
              menu={{
                items: modes.map(({ id, title }) => ({
                  key: id,
                  label: title,
                  onClick: () => navigate(detectionHref(id)),
                })),
                selectedKeys: detecting ? [mode] : [],
                onClick: () => setModeMenuOpen(false),
              }}>
              <button type="button" aria-haspopup="menu" aria-expanded={modeMenuOpen}
                aria-current={detecting ? 'page' : undefined}>
                {item.title}<DownOutlined aria-hidden="true" />
              </button>
            </Dropdown>
          : <a key={item.path} href={item.path} aria-current={path === item.path ? 'page' : undefined}>
              {item.title}
            </a>)}
          </nav>
        </div>
      </GlassSurface>
      <div className="avatar">
        <GlassSurface kind="avatar">
          <a className="avatar-link" href={userName ? ACCOUNT_PATH : LOGIN_PATH}
            aria-current={path === ACCOUNT_PATH ? 'page' : undefined} aria-label={userName ? '当前账号' : '登录 / 注册'}>
            <UserRound size={30} strokeWidth={1.3} fill={userName ? 'currentColor' : 'none'} />
          </a>
        </GlassSurface>
      </div>
    </header>
  )
}

export default function AppShell({ path, userName, mode, navigate, onLinkClick, children }: NavigationProps & {
  onLinkClick: MouseEventHandler<HTMLDivElement>
  children: ReactNode
}) {
  const main = useRef<HTMLElement>(null)
  const previousPath = useRef(path)
  const route = appRouteFor(path)

  useEffect(() => {
    document.title = `${route?.title ?? '页面不存在'} · PhysioSense`
    if (previousPath.current !== path) {
      main.current?.scrollTo({ top: 0 })
      main.current?.focus({ preventScroll: true })
    }
    previousPath.current = path
  }, [path, route?.title])
  return (
    <div className="app-shell" onClick={onLinkClick}>
      {/* 捕获区里的固定浮层（fixed + transform）按库规范标注忽略，不参与背景快照。 */}
      <a className="skip-link" href="#main-content" data-liquid-glass-ignore="">跳到主要内容</a>
      <Topbar path={path} userName={userName} mode={mode} navigate={navigate} />
      <main id="main-content" className="route-content" ref={main} tabIndex={-1} aria-label={route?.title ?? '页面不存在'}>
        <Workspace compact={path === ACCOUNT_PATH}>{children}</Workspace>
      </main>
    </div>
  )
}
