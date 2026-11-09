import { createHttpClient } from '../api/http.js'
import { toast } from './toast.js'

// 管理端的认证与提示策略，和检测端共用传输配置与错误解析。
export default createHttpClient({
  token: () => localStorage.getItem('emotion-token'),
  onError(error) {
    toast.error(error.message)
    if (error.response?.status === 401) {
      localStorage.removeItem('emotion-token')
      localStorage.removeItem('emotion-user')
      if (!window.location.pathname.startsWith('/login')) window.location.href = '/login'
    }
  },
})
