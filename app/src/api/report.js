import request from '@/utils/request'

export const reportList = (pageNum, pageSize) => {
  return request.get('/report', { params: { pageNum, pageSize } })
}

export const queryReports = (params) => request.get('/report', { params })
export const reportDetail = (id) => request.get('/report/detail', { params: { id } })
export const deleteReport = (id) => request.delete('/report/delete', { params: { id } })
export const saveReport = (data) => request.post('/report/save', data)
export const analyzeReports = (data) => request.post('/report/analysis', data)
