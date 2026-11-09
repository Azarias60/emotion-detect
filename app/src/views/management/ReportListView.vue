<script setup>
import { onMounted, reactive, ref } from 'vue'
import { deleteReport, queryReports } from '@/api/report'
const items = ref([]),
  total = ref(0),
  loading = ref(false),
  filters = reactive({
    pageNum: 1,
    pageSize: 10,
    type: '',
    keyword: '',
    startDate: '',
    endDate: '',
  })
const typeMap = {
  health: '健康问诊',
  tongue: '舌象检测',
  fatigue: '疲劳检测',
  emotion: '情绪识别',
  companion: '数字人',
  comprehensive: '综合分析',
}
async function load() {
  loading.value = true
  try {
    const response = await queryReports(filters)
    items.value = response.data.items
    total.value = response.data.total
  } finally {
    loading.value = false
  }
}
async function remove(item) {
  if (!window.confirm(`确定删除“${item.title}”吗？`)) return
  await deleteReport(item.id)
  if (items.value.length === 1 && filters.pageNum > 1) filters.pageNum--
  await load()
}
function changePage(delta) {
  const next = filters.pageNum + delta
  if (next < 1 || next > Math.ceil(total.value / filters.pageSize)) return
  filters.pageNum = next
  load()
}
onMounted(load)
</script>
<template>
  <div>
    <div class="page-head">
      <div>
        <h1>检测报告</h1>
        <p>查询、查看和管理个人检测记录</p>
      </div>
      <RouterLink class="manage-btn" to="/detect/emotion">开始检测</RouterLink>
    </div>
    <section class="manage-panel">
      <div class="filters">
        <select v-model="filters.type" class="manage-input">
          <option value="">全部类型</option>
          <option v-for="(label, key) in typeMap" :key="key" :value="key">
            {{ label }}
          </option></select
        ><input v-model="filters.keyword" class="manage-input" placeholder="标题或摘要" /><input
          v-model="filters.startDate"
          class="manage-input"
          type="date"
        /><input v-model="filters.endDate" class="manage-input" type="date" /><button class="manage-btn" @click="filters.pageNum = 1; load()">
          查询
        </button>
      </div>
      <div class="report-table">
        <div class="table-head">
          <span>类型</span><span>报告信息</span><span>检测时间</span><span>操作</span>
        </div>
        <div v-if="loading" class="manage-empty">加载中...</div>
        <div v-else-if="!items.length" class="manage-empty">暂无符合条件的报告</div>
        <div v-for="item in items" :key="item.id" class="table-row">
          <span class="type-chip">{{ typeMap[item.type] || item.type }}</span>
          <div>
            <strong>{{ item.title }}</strong
            ><small>{{ item.summary || '暂无摘要' }}</small>
          </div>
          <time>{{ item.createTime?.replace('T', ' ').slice(0, 19) }}</time>
          <div class="row-actions">
            <RouterLink :to="`/management/reports/${item.id}`">查看</RouterLink
            ><button @click="remove(item)">删除</button>
          </div>
        </div>
      </div>
      <footer class="pagination">
        <span>共 {{ total }} 条</span
        ><button :disabled="filters.pageNum <= 1" @click="changePage(-1)">上一页</button
        ><b>{{ filters.pageNum }} / {{ Math.max(1, Math.ceil(total / filters.pageSize)) }}</b
        ><button
          :disabled="filters.pageNum >= Math.ceil(total / filters.pageSize)"
          @click="changePage(1)"
        >
          下一页
        </button>
      </footer>
    </section>
  </div>
</template>
<style scoped>
.filters {
  padding: 14px;
  display: grid;
  grid-template-columns: 140px 1fr 150px 150px auto;
  gap: 10px;
  border-bottom: 1px solid rgba(0, 242, 255, 0.12);
}
.table-head,
.table-row {
  display: grid;
  grid-template-columns: 120px 1fr 180px 120px;
  gap: 14px;
  align-items: center;
}
.table-head {
  padding: 11px 16px;
  color: #527981;
  font-size: 11px;
  background: rgba(0, 242, 255, 0.025);
}
.table-row {
  padding: 15px 16px;
  border-bottom: 1px solid rgba(0, 242, 255, 0.08);
}
.type-chip {
  color: #ffac49;
  font-size: 11px;
}
.table-row > div:nth-child(2) {
  display: grid;
  gap: 5px;
}
.table-row strong {
  color: #b9dadd;
  font-size: 13px;
}
.table-row small,
.table-row time {
  color: #54767d;
  font-size: 11px;
}
.row-actions {
  display: flex;
  gap: 10px;
}
.row-actions a,
.row-actions button {
  background: none;
  border: 0;
  color: #00dcea;
  font-size: 11px;
  cursor: pointer;
  text-decoration: none;
}
.row-actions button {
  color: #ff7668;
}
.pagination {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 10px;
  padding: 13px 16px;
  color: #5b7d84;
  font-size: 11px;
}
.pagination button {
  border: 1px solid rgba(0, 242, 255, 0.18);
  background: #00121c;
  color: #79a2a8;
  padding: 6px 10px;
}
.pagination b {
  color: #00e3ef;
}
@media (max-width: 1000px) {
  .filters {
    grid-template-columns: 1fr 1fr;
  }
  .table-head,
  .table-row {
    grid-template-columns: 90px 1fr 120px;
  }
  .table-head span:last-child,
  .table-row > .row-actions {
    grid-column: 2;
  }
  .table-row time {
    font-size: 9px;
  }
}
</style>
