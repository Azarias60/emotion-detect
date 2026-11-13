<script setup>
import { onMounted, ref } from 'vue'
import DetectionNav from './DetectionNav.vue'
import { detectionClient as http, responseData } from '../api/http.js'
import { stopCapture } from '../api/hardware.js'

defineProps({ title: { type: String, required: true }, subtitle: { type: String, default: '' } })

// 互不打扰（2026-11-11）：进入任一检测页即
// ① 停掉上一个模块遗留的硬件采集窗口；② 读取全局检测状态，若另一模块
// 正在分析则显示提示条（后端全局锁同时保证本模块开始会被 409 拒绝）。
const globalBusy = ref('')
onMounted(async () => {
  try { await stopCapture() } catch { /* 没有采集在跑时静默 */ }
  try {
    const status = await responseData(http.get('/detection/status'))
    if (status?.busy) globalBusy.value = status.module
  } catch { /* 后端未升级时静默 */ }
})
</script>

<template>
  <div class="detection-screen">
    <header class="detection-header">
      <div class="brand">
        <span class="brand-mark">CUG</span>
        <div>
          <h1>{{ title }}</h1>
          <p>{{ subtitle }}</p>
        </div>
      </div>
      <DetectionNav />
    </header>
    <div v-if="globalBusy" class="detection-busy-strip">
      「{{ globalBusy }}」正在进行中——检测结果出来前，本模块暂不能开始
    </div>
    <main class="detection-body"><slot /></main>
  </div>
</template>

<style>
:root {
  color-scheme: dark;
  font-family: 'Microsoft YaHei', Arial, sans-serif;
  background: #000c17;
}
* {
  box-sizing: border-box;
}
body {
  margin: 0;
  background: #000c17;
}
body.detection-locked .detection-nav {
  pointer-events: none;
  opacity: 0.42;
  filter: saturate(0.45);
}
.detection-screen {
  min-height: 100vh;
  color: #ccecf0;
  background:
    radial-gradient(circle at 50% 15%, rgba(0, 98, 130, 0.19), transparent 38%),
    linear-gradient(135deg, #000912, #00131f 55%, #000912);
  padding: 0 22px 22px;
}
.detection-header {
  height: 78px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid rgba(0, 242, 255, 0.22);
}
.brand {
  display: flex;
  align-items: center;
  gap: 13px;
}
.brand-mark {
  width: 44px;
  height: 44px;
  display: grid;
  place-items: center;
  border: 1px solid #00f2ff;
  color: #00f2ff;
  font-weight: 800;
  font-size: 12px;
  box-shadow:
    inset 0 0 16px rgba(0, 242, 255, 0.2),
    0 0 10px rgba(0, 242, 255, 0.16);
  clip-path: polygon(
    10px 0,
    100% 0,
    100% calc(100% - 10px),
    calc(100% - 10px) 100%,
    0 100%,
    0 10px
  );
}
.brand h1 {
  margin: 0;
  color: #00f2ff;
  font-size: 22px;
  letter-spacing: 3px;
  text-shadow: 0 0 12px rgba(0, 242, 255, 0.35);
}
.brand p {
  margin: 4px 0 0;
  color: #5b8e99;
  font-size: 11px;
  letter-spacing: 2px;
}
.detection-body {
  padding-top: 18px;
}
.tech-panel {
  border: 1px solid rgba(0, 242, 255, 0.25);
  background: rgba(0, 24, 38, 0.78);
  position: relative;
  box-shadow: inset 0 0 30px rgba(0, 154, 200, 0.035);
}
.tech-panel::before {
  content: '';
  position: absolute;
  top: -1px;
  left: 18px;
  width: 72px;
  height: 2px;
  background: #00f2ff;
  box-shadow: 0 0 8px #00f2ff;
}
.panel-heading {
  padding: 13px 16px;
  border-bottom: 1px solid rgba(0, 242, 255, 0.16);
  color: #00f2ff;
  font-size: 14px;
  letter-spacing: 2px;
}
.tech-button {
  border: 1px solid #00b9cc;
  color: #00f2ff;
  background: rgba(0, 153, 180, 0.12);
  padding: 10px 20px;
  cursor: pointer;
  letter-spacing: 2px;
  transition: 0.2s;
}
.tech-button:hover:not(:disabled) {
  background: rgba(0, 242, 255, 0.2);
  box-shadow: 0 0 13px rgba(0, 242, 255, 0.25);
}
.tech-button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.tech-button.orange {
  color: #ffac45;
  border-color: #c86d13;
  background: rgba(200, 109, 19, 0.12);
}
/* 保存报告（2026-11-13）：金色=存档类主动作，与橙色"分析中"区分 */
.tech-button.gold {
  color: #ffd98a;
  border-color: #d9a441;
  background: rgba(217, 164, 65, 0.14);
}
.tech-button.gold:hover:not(:disabled) {
  background: rgba(217, 164, 65, 0.26);
  box-shadow: 0 0 13px rgba(217, 164, 65, 0.3);
}
.status-tag {
  display: inline-flex;
  padding: 3px 8px;
  border: 1px solid rgba(0, 242, 255, 0.3);
  color: #65cbd3;
  font-size: 11px;
}
.status-tag.pending {
  border-color: rgba(255, 157, 46, 0.35);
  color: #ff9d2e;
}
.empty-value {
  color: #557680;
}
.error-text {
  color: #ff786f;
}
@media (max-width: 900px) {
  .detection-header {
    height: auto;
    padding: 14px 0;
    align-items: flex-start;
    gap: 12px;
    flex-direction: column;
  }
  .detection-screen {
    padding: 0 12px 18px;
  }
}
.detection-busy-strip {
  margin: 0 2px 10px;
  padding: 10px 16px;
  border: 1px solid #d9a441;
  border-radius: 8px;
  background: rgba(217, 164, 65, 0.12);
  color: #ffd98a;
  font-size: 13.5px;
  letter-spacing: 0.5px;
}
</style>
