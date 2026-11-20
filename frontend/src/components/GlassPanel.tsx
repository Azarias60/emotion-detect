import type { ReactNode } from 'react'
import { LiquidGlass } from 'simple-liquid-glass'
import { LiquidGlassScene } from 'simple-liquid-glass/backdrop'

export function GlassScene({ children }: { children: ReactNode }) {
  return <LiquidGlassScene className="glass-scene">
    <section className="scene-bg-layer" aria-hidden="true" inert />
    <section className="scene-content">{children}</section>
  </LiquidGlassScene>
}

const appearance = {
  workspace: {
    radius: 20,
    glassColor: 'rgba(14, 28, 50, 0.72)',
    borderColor: 'rgba(96, 165, 250, 0.22)',
    saturation: 100,
    optics: 'liquidgl',
    displace: 4,
    frost: 0.14,
    lightness: 58,
  },
  nav: {
    radius: 18,
    glassColor: 'rgba(14, 28, 50, 0.66)',
    borderColor: 'rgba(96, 165, 250, 0.26)',
    saturation: 110,
    blur: 2,
    optics: 'liquidgl',
    displace: 3,
    frost: 0.12,
  },
  avatar: {
    glassColor: 'rgba(14, 28, 50, 0.72)',
    autoTextColor: true,
  },
} as const

export function GlassSurface({ kind, children }: {
  kind: keyof typeof appearance
  children: ReactNode
}) {
  return <LiquidGlass id={kind} {...appearance[kind]}>
    {children}
  </LiquidGlass>
}
