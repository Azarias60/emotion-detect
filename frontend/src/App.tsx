import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent } from 'react'
import AccountPage from './pages/AccountPage'
import ReportsScreen from './app/ReportsScreen'
import DigitalHumanScreen from './app/DigitalHumanScreen'
import HomeScreen from './app/HomeScreen'
import DetectionScreen from './app/DetectionScreen'
import { assembleReportStore } from './bootstrap/reports'
import { assembleObjectCollections, assembleSessionGateways, createDigitalHumanTransport, createAvatarStudioPort, createVoiceClonePort, createLlmSettingsPort, createGuideTransport, type AssembledCollections } from './bootstrap/digitalHuman'
import { createHttpDetection, readCaptureStatus, createDevicePreview } from './bootstrap/detection'
import { useDevicePreview } from './app/useDevicePreview'
import AppShell from './app/AppShell'
import AuthShell from './app/AuthShell'
import { GlassScene } from './components/GlassPanel'
import { UiProvider } from './app/UiProvider'
import AuthScreen from './app/AuthScreen'
import { PlaceholderPage } from './pages/PlaceholderPage'
import { auth } from './bootstrap/auth'
import { ACCOUNT_PATH, DIGITAL_HUMAN_PATH, LOGIN_PATH, isDetectionPath, isPhysiologyPath, normalizePath } from './app/routes'
import { isMode, type DetectionMode } from './domain/detection'
import type { Session } from './domain/auth'

const currentUrl = () => location.pathname + location.search

export default function App() {
  const [session, setSession] = useState<Session | null>(() => auth.current())
  return <UiProvider><GlassScene><SessionApp key={session?.token ?? ''} session={session} setSession={setSession} /></GlassScene></UiProvider>
}

/** 登录装配工作区；一次登录拥有其全部使用期。 */
function SessionApp({ session, setSession }: {
  session: Session | null
  setSession: (session: Session | null) => void
}) {
  const [url, setUrl] = useState(currentUrl)
  const route = new URL(url, location.origin)
  const path = normalizePath(route.pathname)
  const query = route.searchParams
  const requestedMode = query.get('mode')
  const [lastMode, setLastMode] = useState<DetectionMode>('health')
  const mode = isDetectionPath(path) && isMode(requestedMode) ? requestedMode : lastMode
  const token = session?.token
  const onDetectionPage = isDetectionPath(path)
  const devicePreview = useMemo(() => token ? createDevicePreview(token) : null, [token])
  const preview = useDevicePreview(readCaptureStatus, devicePreview, onDetectionPage)
  const store = useMemo(() => assembleReportStore(token ?? null), [token])
  const gateway = useMemo(() => token ? createHttpDetection(token) : null, [token])
  const sessionGateways = useMemo(() => token ? assembleSessionGateways(token) : null, [token])
  // 数字人引擎能力端口：实时口型/对话走引擎（8020），制作与克隆走产线后端（8081），
  // 模型 Key 写引擎本机配置热生效——端口只在此装配一次
  const engineTransport = useMemo(() => createDigitalHumanTransport(), [])
  const avatarStudioPort = useMemo(() => createAvatarStudioPort(), [])
  const voiceClonePort = useMemo(() => createVoiceClonePort(), [])
  const llmPort = useMemo(() => createLlmSettingsPort(), [])
  const guideTransport = useMemo(() => createGuideTransport(), [])
  const objectCollections = useMemo(() => sessionGateways && token
    ? assembleObjectCollections(sessionGateways, token) : null, [sessionGateways, token])
  useEffect(() => () => store?.stop(), [store])
  useEffect(() => () => objectCollections?.stop(), [objectCollections])
  // 退出登录/切换账号：本会话持有的两条引擎通道（暖暖 WS + 数字人 WebRTC/WS）
  // 随组合根一起释放；SPA 换页不经过这里（换页只 suspend，不销毁）。
  // generation 守卫：StrictMode 的"卸载后立刻重挂"不能误关刚建好的通道。
  const sessionMounts = useRef(0)
  useEffect(() => {
    sessionMounts.current += 1
    const generation = sessionMounts.current
    return () => {
      queueMicrotask(() => {
        if (sessionMounts.current !== generation) return
        engineTransport.closeAll()
        guideTransport.closeAll()
      })
    }
  }, [engineTransport, guideTransport])

  useEffect(() => {
    if (onDetectionPage && isMode(requestedMode)) setLastMode(requestedMode)
  }, [onDetectionPage, requestedMode])

  const navigate = useCallback((to: string) => {
    const next = new URL(to, location.origin)
    if (next.origin !== location.origin) return
    const target = next.pathname + next.search
    if (target !== currentUrl()) history.pushState(null, '', target)
    setUrl(currentUrl())
  }, [])

  function onLinkClick(event: MouseEvent<HTMLDivElement>) {
    if (event.defaultPrevented || event.button !== 0 ||
        event.metaKey || event.ctrlKey || event.shiftKey || event.altKey ||
        !(event.target instanceof Element)) return
    const link = event.target.closest<HTMLAnchorElement>('a[href]')
    if (!link || !event.currentTarget.contains(link) || link.target || link.hasAttribute('download')) return
    const target = new URL(link.href, location.href)
    // 原生页面内锚点和站外导航均交还浏览器。
    if (target.origin !== location.origin || target.hash) return
    event.preventDefault()
    navigate(target.pathname + target.search)
  }

  useEffect(() => {
    const onPopState = () => setUrl(currentUrl())
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  // 统一先登录：工作区所有页面都在认证之后，入口据身份装配。
  useEffect(() => {
    if (session) return
    const route = new URL(url, location.origin)
    if (normalizePath(route.pathname) === LOGIN_PATH) return
    history.replaceState({ ...history.state }, '',
      `${LOGIN_PATH}?returnTo=${encodeURIComponent(route.pathname + route.search)}`)
    setUrl(currentUrl())
  }, [session, url])

  function authenticated(next: Session) {
    setSession(next)
    const returnTo = query.get('returnTo')
    navigate(returnTo?.startsWith('/') && !returnTo.startsWith('//') ? returnTo : '/')
  }
  async function logout() {
    try { await auth.logout(session) } catch { /* 断网时依然清理会话 */ }
    setSession(null)
    navigate(LOGIN_PATH)
  }

  if (!session) {
    return <AuthShell><AuthScreen service={auth} onAuthenticated={authenticated} /></AuthShell>
  }
  const page = path === '/' && sessionGateways ? <HomeScreen
        gateways={sessionGateways} active={path === '/'} guideTransport={guideTransport} onNavigate={navigate} />
    : isDetectionPath(path) ? <DetectionScreen
        store={store!} gateway={gateway!}
        mode={mode} physiology={isPhysiologyPath(path)}
        preview={{ capture: preview.capture.capture ?? null, stream: preview.capture.stream ?? null }} />
    : path === ACCOUNT_PATH ? <AccountPage userName={session.user.username} onLogout={logout} />
    : path === '/reports' ? <ReportsScreen
        store={store} requestedId={Number(query.get('id')) || null}
        onSelect={id => navigate(id ? `/reports?id=${id}` : '/reports')} />
    : path === DIGITAL_HUMAN_PATH && sessionGateways && objectCollections ? <DigitalHumanScreen
        gateways={sessionGateways} collections={objectCollections as AssembledCollections}
        active={path === '/digital-human'}
        engineTransport={engineTransport} studioPort={avatarStudioPort}
        clonePort={voiceClonePort} llmPort={llmPort} />
    : <PlaceholderPage />

  return <AppShell path={path} userName={session.user.username} mode={mode} navigate={navigate}
    onLinkClick={onLinkClick}>{page}</AppShell>
}
