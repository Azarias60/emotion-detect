import { createHttpDetection, readCaptureStatus } from '../api/detection'
import { connectReportStream, createHttpReports } from '../api/reports'
import { createDevicePreview } from '../api/preview'

/** 组合根：低层实现仅在此装配，页面和应用用例依赖抽象端口。 */
export { createHttpDetection, readCaptureStatus, connectReportStream, createHttpReports, createDevicePreview }
