import { connectReportStream, createHttpReports } from '../api/reports'
import { createReportStore, type ReportStore } from '../application/reports'

/** 仅组合根连接上层业务端口与后端具体实现。 */
export function assembleReportStore(token: string | null): ReportStore | null {
  return token ? createReportStore(
    createHttpReports(token),
    onFacts => connectReportStream(token, onFacts),
  ) : null
}
