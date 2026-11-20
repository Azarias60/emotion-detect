import type { ReactNode } from 'react'
import { GlassSurface } from './GlassPanel'

export function Workspace({ compact, children }: { compact?: boolean; children: ReactNode }) {
  return <div className={compact ? 'workspace workspace-compact' : 'workspace'}>
    <GlassSurface kind="workspace">
      <div className="workspace-body">{children}</div>
    </GlassSurface>
  </div>
}

export function SectionHeading({ title, children }: { title: string; children?: ReactNode }) {
  return <header className="section-heading">
    <h2>{title}</h2>
    {children}
  </header>
}

export function WorkMessage({ title, detail, children }: { title: string; detail?: string; children?: ReactNode }) {
  return <div className="work-message" role="status">
    <strong>{title}</strong>
    {detail && <p>{detail}</p>}
    {children}
  </div>
}

export function WorkError({ message }: { message: string }) {
  return message ? <p className="work-error" role="alert">{message}</p> : null
}
