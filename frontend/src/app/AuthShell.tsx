import { useEffect, type PropsWithChildren } from 'react'
import { Workspace } from '../components/WorkSurface'

export default function AuthShell({ children }: PropsWithChildren) {
  useEffect(() => { document.title = '登录 · PhysioSense' }, [])
  return (
    <div className="app-shell auth-shell">
      {/* 捕获区里的固定浮层（fixed + transform）按库规范标注忽略，不参与背景快照。 */}
      <a className="skip-link" href="#main-content" data-liquid-glass-ignore="">跳到主要内容</a>
      <main id="main-content" className="auth-content" tabIndex={-1}>
        <Workspace compact>{children}</Workspace>
      </main>
    </div>
  )
}
