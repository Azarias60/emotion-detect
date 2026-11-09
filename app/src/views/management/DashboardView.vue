<script setup>
import { computed, onMounted, ref } from 'vue'
import { queryReports } from '@/api/report'
const reports = ref([]),
  total = ref(0),
  loading = ref(true)
const typeMap = {
  health: '健康问诊',
  tongue: '舌象检测',
  fatigue: '疲劳检测',
  emotion: '情绪识别',
  companion: '数字人',
  comprehensive: '综合分析',
}
const stats = computed(() => {
  const counts = { health: 0, tongue: 0, fatigue: 0, emotion: 0 }
  reports.value.forEach((item) => {
    if (item.type in counts) counts[item.type]++
  })
  return counts
})
onMounted(async () => {
  try {
    const response = await queryReports({ pageNum: 1, pageSize: 100 })
    reports.value = response.data.items
    total.value = response.data.total
  } finally {
    loading.value = false
  }
})
</script>
<template>
  <div>
    <div class="page-head">
      <div>
        <h1>管理概览</h1>
        <p>查看个人检测数据与最近报告</p>
      </div>
      <RouterLink class="manage-btn" to="/detect/health">开始新检测</RouterLink>
    </div>
    <div class="stat-grid">
      <div class="stat-card">
        <span>全部报告</span><strong>{{ total }}</strong
        ><small>累计检测记录</small>
      </div>
      <div class="stat-card">
        <span>健康问诊</span><strong>{{ stats.health + stats.tongue }}</strong
        ><small>心率与舌象</small>
      </div>
      <div class="stat-card">
        <span>疲劳检测</span><strong>{{ stats.fatigue }}</strong
        ><small>行为与生理指标</small>
      </div>
      <div class="stat-card">
        <span>情绪识别</span><strong>{{ stats.emotion }}</strong
        ><small>多模态情绪分析</small>
      </div>
    </div>
    <section class="manage-panel recent-panel">
      <div class="manage-panel-title">最近检测记录</div>
      <div v-if="loading" class="manage-empty">数据加载中...</div>
      <div v-else-if="!reports.length" class="manage-empty">
        暂无检测记录，完成检测后报告会显示在这里
      </div>
      <RouterLink
        v-for="item in reports.slice(0, 6)"
        :key="item.id"
        :to="`/management/reports/${item.id}`"
        class="recent-row"
        ><span class="report-type">{{ typeMap[item.type] || item.type }}</span>
        <div>
          <strong>{{ item.title }}</strong
          ><small>{{ item.summary || '暂无摘要' }}</small>
        </div>
        <time>{{ item.createTime?.replace('T', ' ').slice(0, 16) }}</time
        ><b>›</b></RouterLink
      >
    </section>
  </div>
</template>
<style scoped>
.stat-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 15px;
  margin-bottom: 20px;
}
.stat-card {
  border: 1px solid rgba(0, 242, 255, 0.18);
  background: linear-gradient(135deg, rgba(0, 242, 255, 0.055), rgba(0, 16, 26, 0.85));
  padding: 18px;
  display: grid;
  gap: 6px;
  position: relative;
}
.stat-card::after {
  content: '';
  position: absolute;
  right: 12px;
  top: 12px;
  width: 28px;
  height: 28px;
  border-top: 1px solid rgba(255, 157, 46, 0.4);
  border-right: 1px solid rgba(255, 157, 46, 0.4);
}
.stat-card span {
  color: #71979e;
  font-size: 12px;
}
.stat-card strong {
  font-size: 30px;
  color: #00f2ff;
}
.stat-card small {
  color: #456b73;
  font-size: 10px;
}
.recent-row {
  display: grid;
  grid-template-columns: 90px 1fr 150px 20px;
  gap: 14px;
  align-items: center;
  padding: 14px 16px;
  border-bottom: 1px solid rgba(0, 242, 255, 0.08);
  text-decoration: none;
  color: #96b7bc;
}
.recent-row:hover {
  background: rgba(0, 242, 255, 0.04);
}
.report-type {
  color: #ffab46;
  font-size: 11px;
}
.recent-row div {
  display: grid;
  gap: 4px;
}
.recent-row strong {
  font-size: 13px;
  color: #bcdde0;
}
.recent-row small {
  color: #52767e;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.recent-row time {
  color: #52767e;
  font-size: 11px;
}
.recent-row b {
  color: #00f2ff;
}
@media (max-width: 1000px) {
  .stat-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
