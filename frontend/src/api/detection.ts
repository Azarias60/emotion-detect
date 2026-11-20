import { api } from './client'
import type { CaptureStatus } from '../domain/device'
import type { DetectionGateway, DetectionMode } from '../domain/detection'

export const readCaptureStatus = () => api<CaptureStatus>('/capture/status')

/** 意图传输：启动只提交模式与身份；准备、采集、问答与记录由后端承担。 */
export function createHttpDetection(token: string): DetectionGateway {
  const intent = (reportId: number, path: string, body?: unknown) =>
    api<null>(`/detections/${reportId}${path}`, { method: 'POST', body, token }).then(() => undefined)

  return {
    begin: mode => api<{ reportId: number }>('/detections', {
      method: 'POST', body: { kind: mode }, token,
    }),
    finish: reportId => intent(reportId, '/finish'),
    cancel: reportId => intent(reportId, '/cancel'),
    retry: reportId => intent(reportId, '/retry'),
    expressUsage: (reportId, mode) => intent(reportId, '/usage', { mode }),
    questionDisplayed: (reportId, index) => intent(reportId, '/questions/displayed', { index }),
  }
}
