<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { deleteReport, reportDetail } from '@/api/report'
const route = useRoute(),
  router = useRouter(),
  report = ref(null),
  loading = ref(true),
  error = ref('')
const typeMap = {
  health: '健康问诊',
  tongue: '舌象检测',
  fatigue: '疲劳检测',
  emotion: '情绪识别',
  companion: '数字人',
  comprehensive: '综合分析',
}
const formattedResult = computed(() => JSON.stringify(report.value?.result || {}, null, 2))
onMounted(async () => {
  try {
    const response = await reportDetail(route.params.id)
    report.value = response.data
  } catch (reason) {
    error.value = reason.response?.data?.detail || '报告加载失败'
  } finally {
    loading.value = false
  }
})
async function remove() {
  if (!window.confirm('确定删除当前报告吗？')) return
  await deleteReport(route.params.id)
  router.replace('/management/reports')
}
</script>
<template>
  <div>
    <div class="page-head">
      <div>
        <h1>报告详情</h1>
        <p>查看检测结论和结构化数据</p>
      </div>
      <div class="detail-actions">
        <RouterLink class="manage-btn" to="/management/reports">返回列表</RouterLink
        ><button class="manage-btn warn" @click="remove">删除报告</button>
      </div>
    </div>
    <div v-if="loading" class="manage-panel manage-empty">报告加载中...</div>
    <div v-else-if="error" class="manage-panel manage-empty manage-error">{{ error }}</div>
    <div v-else class="detail-grid">
      <section class="manage-panel summary-panel">
        <div class="manage-panel-title">基本信息</div>
        <dl>
          <dt>报告类型</dt>
          <dd>{{ typeMap[report.type] || report.type }}</dd>
          <dt>报告标题</dt>
          <dd>{{ report.title }}</dd>
          <dt>检测时间</dt>
          <dd>{{ report.createTime?.replace('T', ' ') }}</dd>
          <dt>结果摘要</dt>
          <dd>{{ report.summary || '暂无摘要' }}</dd>
        </dl>
      </section>
      <section class="manage-panel raw-panel">
        <div class="manage-panel-title">分析报告</div>
        <div class="report-text">{{ report.rawReport || '暂无文本分析报告' }}</div>
      </section>
      <section class="manage-panel json-panel">
        <div class="manage-panel-title">结构化检测数据</div>
        <pre>{{ formattedResult }}</pre>
      </section>
    </div>
  </div>
</template>
<style scoped>
.detail-actions {
  display: flex;
  gap: 9px;
}
.detail-grid {
  display: grid;
  grid-template-columns: 340px 1fr;
  gap: 16px;
}
.summary-panel {
  grid-row: 1/3;
}
.summary-panel dl {
  padding: 10px 18px;
}
.summary-panel dt {
  color: #557a82;
  font-size: 10px;
  margin-top: 15px;
}
.summary-panel dd {
  margin: 5px 0;
  color: #acd0d4;
  font-size: 13px;
  line-height: 1.6;
}
.report-text {
  padding: 20px;
  white-space: pre-wrap;
  color: #a0c0c4;
  line-height: 1.9;
  font-size: 13px;
  min-height: 180px;
}
.json-panel pre {
  margin: 0;
  padding: 18px;
  max-height: 350px;
  overflow: auto;
  color: #69cbd2;
  background: rgba(0, 8, 14, 0.6);
  font-size: 11px;
}
.raw-panel,
.json-panel {
  min-width: 0;
}
@media (max-width: 850px) {
  .detail-grid {
    grid-template-columns: 1fr;
  }
  .summary-panel {
    grid-row: auto;
  }
}
</style>
