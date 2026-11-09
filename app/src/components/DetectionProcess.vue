<script setup>
import { computed } from 'vue'

const props = defineProps({
  phase: { type: String, default: 'idle' },
  progress: { type: Number, default: 0 },
  remaining: { type: Number, default: 0 },
  message: { type: String, default: '' },
})

const visible = computed(
  () => !['idle', 'ready', 'complete', 'error', 'tongue'].includes(props.phase)
)
const blocking = computed(() =>
  ['uploading', 'analyzing', 'tongue-analyzing'].includes(props.phase)
)
const title = computed(
  () =>
    ({
      preparing: '正在准备检测设备',
      capturing: '正在采集检测数据',
      consulting: '正在进行健康问诊',
      uploading: '正在上传检测数据',
      analyzing: 'AI 模型正在分析',
      'tongue-capturing': '正在截取舌象图片',
      'tongue-analyzing': '正在分析舌象特征',
    })[props.phase] || '检测任务处理中'
)
</script>

<template>
  <div v-if="visible" class="process-layer" :class="{ blocking }">
    <div class="process-card">
      <div class="process-spinner">
        <i></i><span>{{ Math.max(0, Math.min(100, progress)) }}%</span>
      </div>
      <div class="process-copy">
        <strong>{{ title }}</strong>
        <p>{{ message || '请勿刷新、离开页面或重复点击操作按钮' }}</p>
        <small v-if="remaining > 0">剩余约 {{ remaining }} 秒</small>
      </div>
    </div>
  </div>
</template>

<style scoped>
.process-layer {
  position: fixed;
  left: 50%;
  top: 91px;
  transform: translateX(-50%);
  z-index: 80;
  pointer-events: none;
}
.process-layer.blocking {
  inset: 0;
  transform: none;
  display: grid;
  place-items: center;
  background: rgba(0, 7, 13, 0.72);
  backdrop-filter: blur(3px);
  pointer-events: all;
}
.process-card {
  min-width: 390px;
  max-width: calc(100vw - 28px);
  display: flex;
  align-items: center;
  gap: 17px;
  padding: 14px 22px;
  border: 1px solid rgba(0, 242, 255, 0.52);
  background: rgba(0, 18, 30, 0.95);
  box-shadow: 0 0 30px rgba(0, 200, 240, 0.2);
  clip-path: polygon(
    12px 0,
    100% 0,
    100% calc(100% - 12px),
    calc(100% - 12px) 100%,
    0 100%,
    0 12px
  );
}
.process-spinner {
  width: 56px;
  height: 56px;
  border: 2px solid rgba(0, 242, 255, 0.18);
  border-top-color: #00f2ff;
  border-bottom-color: #ff9d2e;
  border-radius: 50%;
  display: grid;
  place-items: center;
  animation: turn 1.5s linear infinite;
}
.process-spinner i {
  position: absolute;
  width: 43px;
  height: 43px;
  border: 1px dashed rgba(0, 242, 255, 0.38);
  border-radius: 50%;
}
.process-spinner span {
  font-size: 10px;
  color: #00f2ff;
  animation: turn-reverse 1.5s linear infinite;
}
.process-copy {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.process-copy strong {
  color: #00f2ff;
  font-size: 14px;
  letter-spacing: 2px;
}
.process-copy p {
  margin: 0;
  color: #83aab1;
  font-size: 11px;
}
.process-copy small {
  color: #ffad4a;
  font-size: 10px;
}
@keyframes turn {
  to {
    transform: rotate(360deg);
  }
}
@keyframes turn-reverse {
  to {
    transform: rotate(-360deg);
  }
}
@media (max-width: 520px) {
  .process-card {
    min-width: 0;
    width: calc(100vw - 28px);
  }
}
</style>
