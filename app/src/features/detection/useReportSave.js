import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { createHttpClient } from '../../api/http.js'

// 保存报告要带 token（/report/save 要求登录），但不能挂 utils/request 那个
// "401 就自动跳 /login"的 onError——保存失败要弹确认让用户自己决定
const client = createHttpClient({ token: () => localStorage.getItem('emotion-token'), timeout: 30000 })

/**
 * 检测报告保存（2026-11-13 新增）：三个检测页共用。
 *
 * 背景：后端 /report/save 与管理后台（报告列表/仪表盘/历史分析）一直都好，
 * 但检测完成页从来没有保存入口——报告列表永远是空的，管理后台的
 * "最近检测记录/历史分析"全是空壳，首页的"暖暖读上次结论"也无数据可读。
 * 同类产品（各类健康检测小程序）检测完都是一键保存 + 历史趋势。
 *
 * options.navigate / options.post 仅供测试注入；默认走 vue-router 与 axios。
 */
export function useReportSave({ navigate, post } = {}) {
  const saving = ref(false)
  const saved = ref(false)
  const error = ref('')
  const router = navigate ? null : useRouter()
  const go = navigate || ((to) => router.push(to))
  const send = post || ((path, body) => client.post(path, body))

  async function save({ type, title, summary = '', result = {}, rawReport = '' }) {
    if (saving.value || saved.value) return false
    saving.value = true
    error.value = ''
    try {
      await send('/report/save', { type, title, summary, result, rawReport })
      saved.value = true
      return true
    } catch (reason) {
      const status = reason?.response?.status || reason?.cause?.response?.status
      if (status === 401) {
        // 未登录：同类产品做法——告知需要登录，用户确认后再跳（带 redirect 回来）
        if (window.confirm('保存报告需要先登录，是否现在登录？')) {
          go({ path: '/login', query: { redirect: window.location.pathname + window.location.search } })
        }
        error.value = '未登录，报告未保存'
      } else {
        error.value = reason?.message || '保存失败，请重试'
      }
      return false
    } finally {
      saving.value = false
    }
  }

  function reset() {
    saved.value = false
    error.value = ''
  }

  return { saving, saved, error, save, reset }
}
