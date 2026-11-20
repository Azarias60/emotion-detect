import type { DetectionMode } from '../domain/detection'

// 路由与页面名称的唯一事实来源。业务页可以独立演进，Shell 不导入页面内部配置。
export const LOGIN_PATH = '/login'
export const DETECTION_PATH = '/detection'
export const PHYSIOLOGY_PATH = '/detection/physiology'
export const ACCOUNT_PATH = '/account'
export const DIGITAL_HUMAN_PATH = '/digital-human'
export const isDetectionPath = (path: string) => path === DETECTION_PATH || path === PHYSIOLOGY_PATH
export const isPhysiologyPath = (path: string) => path === PHYSIOLOGY_PATH
export const detectionHref = (mode: DetectionMode) => `${DETECTION_PATH}?mode=${mode}`

export const navigation = [
  { path: '/', title: '首页' },
  { path: DETECTION_PATH, title: '检测中心' },
  { path: '/reports', title: '报告管理' },
  { path: DIGITAL_HUMAN_PATH, title: '数字人' },
] as const

export function normalizePath(pathname: string) {
  return pathname.replace(/\/+$/, '') || '/'
}

export function appRouteFor(path: string) {
  if (path === PHYSIOLOGY_PATH) return { title: '生理窗口' }
  if (path === ACCOUNT_PATH) return { title: '当前账号' }
  return navigation.find(item => item.path === path)
}
