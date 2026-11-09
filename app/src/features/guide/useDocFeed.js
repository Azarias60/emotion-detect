/**
 * 全局资料投递口：任何页面把"需要给人看的重要资料"推进来，
 * 首页右侧资料栏（以及后续任何挂载 DocCard 的容器）都会显示。
 *
 * 用法：
 *   import { pushDoc, clearDoc } from '@/composables/useDocFeed'
 *   pushDoc({ title: '检测结果', points: ['...', '...'], tag: '健康问诊' })
 *
 * 设计：模块级单例（不依赖组件树），页面跳转不丢；同一标题重复推送只更新不叠加。
 */
import { ref } from 'vue'

export const feedDoc = ref(null)
const history = []

export function pushDoc(doc) {
  if (!doc || !doc.title) return
  feedDoc.value = {
    kind: doc.kind || 'doc',
    title: doc.title,
    desc: doc.desc || '',
    points: Array.isArray(doc.points) ? doc.points.slice(0, 8) : [],
    tag: doc.tag || '',
    path: doc.path || '',
    ts: Date.now(),
  }
  history.unshift(feedDoc.value)
  if (history.length > 20) history.pop()
}

export function clearDoc() {
  feedDoc.value = null
}

export function docHistory() {
  return history.slice()
}
