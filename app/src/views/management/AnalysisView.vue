<script setup>
import { reactive, ref } from 'vue'
import { analyzeReports } from '@/api/report'
const form = reactive({ startDate: '', endDate: '' }),
  loading = ref(false),
  result = ref(null),
  error = ref('')
async function analyze() {
  error.value = ''
  result.value = null
  if (form.startDate && form.endDate && form.startDate > form.endDate) {
    error.value = '开始日期不能晚于结束日期'
    return
  }
  loading.value = true
  try {
    const response = await analyzeReports({
      startDate: form.startDate || null,
      endDate: form.endDate || null,
    })
    result.value = response.data
  } catch (reason) {
    error.value = reason.response?.data?.detail || '综合分析失败'
  } finally {
    loading.value = false
  }
}
</script>
<template>
  <div>
    <div class="page-head">
      <div>
        <h1>综合分析</h1>
        <p>汇总指定时间范围内的个人检测报告</p>
      </div>
    </div>
    <div class="analysis-grid">
      <section class="manage-panel selector">
        <div class="manage-panel-title">分析范围</div>
        <div class="selector-body">
          <label
            ><span>开始日期</span
            ><input v-model="form.startDate" class="manage-input" type="date" /></label
          ><label
            ><span>结束日期</span><input v-model="form.endDate" class="manage-input" type="date"
          /></label>
          <p>不选择日期时，将分析全部历史检测报告。分析结果会自动保存为一份综合报告。</p>
          <p v-if="error" class="manage-error">{{ error }}</p>
          <button class="manage-btn" :disabled="loading" @click="analyze">
            {{ loading ? '正在汇总...' : '生成综合分析' }}
          </button>
        </div>
      </section>
      <section class="manage-panel analysis-result">
        <div class="manage-panel-title">综合分析结果</div>
        <div v-if="!result" class="manage-empty">选择日期范围并开始分析</div>
        <div v-else class="result-body">
          <div class="analysis-count">
            <strong>{{ result.reportCount }}</strong
            ><span>份报告参与分析</span>
          </div>
          <div class="type-counts">
            <span v-for="(count, type) in result.typeCounts" :key="type"
              >{{ type }} <b>{{ count }}</b></span
            >
          </div>
          <article>{{ result.analysis }}</article>
          <RouterLink class="manage-btn" :to="`/management/reports/${result.id}`"
            >查看已保存报告</RouterLink
          >
        </div>
      </section>
    </div>
  </div>
</template>
<style scoped>
.analysis-grid {
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: 16px;
}
.selector-body {
  padding: 20px;
  display: grid;
  gap: 16px;
}
.selector-body label {
  display: grid;
  gap: 7px;
}
.selector-body label span {
  color: #73969d;
  font-size: 11px;
}
.selector-body p {
  color: #50757d;
  font-size: 11px;
  line-height: 1.7;
}
.analysis-result {
  min-height: 470px;
}
.result-body {
  padding: 24px;
}
.analysis-count {
  display: flex;
  align-items: baseline;
  gap: 9px;
  padding-bottom: 17px;
  border-bottom: 1px solid rgba(0, 242, 255, 0.1);
}
.analysis-count strong {
  font-size: 38px;
  color: #00f2ff;
}
.analysis-count span {
  color: #6b9198;
  font-size: 12px;
}
.type-counts {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin: 17px 0;
}
.type-counts span {
  border: 1px solid rgba(255, 157, 46, 0.22);
  padding: 6px 10px;
  color: #9a805f;
  font-size: 11px;
}
.type-counts b {
  color: #ffad49;
  margin-left: 5px;
}
.result-body article {
  white-space: pre-wrap;
  line-height: 2;
  color: #9cbcc0;
  font-size: 13px;
  margin: 20px 0 28px;
}
@media (max-width: 800px) {
  .analysis-grid {
    grid-template-columns: 1fr;
  }
}
</style>
