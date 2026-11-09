<script setup>
import { computed } from 'vue'
import { videoFeedUrl } from '@/api/http'
import DetectionShell from '@/components/DetectionShell.vue'
import DetectionProcess from '@/components/DetectionProcess.vue'
import { useDetectionGuard } from '@/composables/useDetectionGuard'
import { useFatigueDetection } from '@/features/detection/useFatigueDetection'
import { fatigueMetrics, scalePercent } from '@/utils/detectionMetrics'

const {
  phase,
  busy,
  remaining,
  displayProgress,
  result,
  recording,
  analyzing,
  errorMessage,
  start,
  stop,
} = useFatigueDetection()
useDetectionGuard(busy)
const metrics = computed(() => fatigueMetrics(result.value))
const metricPercent = (metric) => scalePercent(metric.value, 0, metric.max)
function startDetection() {
  if (
    window.confirm(
      '本次疲劳检测将持续约 1 分钟。请自然正视硬件摄像头。检测过程中请勿切换页面。是否开始？'
    )
  )
    start()
}
function requestStopDetection() {
  if (recording.value && window.confirm('提前结束可能导致结果不准确。确定结束并分析当前数据吗？'))
    stop()
}
</script>

<template>
  <DetectionShell title="疲劳检测" subtitle="FATIGUE, BLINK, YAWN & HEART-RATE ANALYSIS">
    <DetectionProcess
      :phase="phase"
      :progress="displayProgress"
      :remaining="remaining"
      :message="phase === 'capturing' ? '正在检测眨眼、哈欠、点头、闭眼特征与硬件心率信号' : ''"
    />
    <div class="fatigue-dashboard">
      <div class="dashboard-main">
        <section class="tech-panel camera-zone">
          <div class="panel-heading live-heading">
            <span class="live-dot"></span>实时画面 <small>非接触式检测</small>
          </div>
          <div class="camera-frame">
            <img
              :src="videoFeedUrl"
              alt="硬件摄像头实时画面"
              style="width: 100%; height: 100%; object-fit: cover"
            />
            <div class="camera-corners"><i></i><i></i><i></i><i></i></div>
            <div class="face-guide"><span></span><small>请正视摄像头并保持自然状态</small></div>
            <i v-if="recording" class="scan-line"></i>
            <div class="camera-badge"><b>AI</b><span>人脸疲劳特征追踪</span></div>
          </div>
          <div class="record-state">
            <span :class="{ active: recording }"></span
            >{{
              recording
                ? `检测采集中，剩余 ${remaining} 秒`
                : result
                  ? '分析完成，可重新检测'
                  : '等待开始检测'
            }}
          </div>
          <div class="actions">
            <button class="tech-button" :disabled="busy" @click="startDetection">
              开始一分钟检测</button
            ><button
              class="tech-button orange"
              :disabled="!recording || analyzing"
              @click="requestStopDetection"
            >
              提前结束并分析
            </button>
          </div>
          <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
        </section>

        <section class="tech-panel analysis-panel">
          <div class="panel-heading">疲劳分析结果 <small>FATIGUE DATA ANALYSIS</small></div>
          <div class="metric-grid">
            <article v-for="metric in metrics" :key="metric.label" class="metric-card">
              <div class="metric-title">
                <span>{{ metric.icon }}</span
                >{{ metric.label }}
              </div>
              <strong v-if="metric.value != null"
                >{{ metric.value }} <em>{{ metric.unit }}</em></strong
              >
              <div class="metric-wave" v-if="metric.value != null"></div>
              <div class="metric-track">
                <i v-if="metric.value != null" :style="{ width: `${metricPercent(metric)}%` }"></i>
              </div>
            </article>
          </div>
          <section class="hrv-block">
            <div class="hrv-head">
              <span>HRV</span>
              <div>
                <small>KDE-HRV 心率变异性</small
                ><strong v-if="result?.heart_rate?.hrv_indices">分析完成</strong>
              </div>
            </div>
            <div class="hrv">
              <span v-if="result?.heart_rate?.hrv_indices?.SDNN != null"
                >SDNN <b>{{ result.heart_rate.hrv_indices.SDNN }}</b> ms</span
              ><span v-if="result?.heart_rate?.hrv_indices?.RMSSD != null"
                >RMSSD <b>{{ result.heart_rate.hrv_indices.RMSSD }}</b> ms</span
              ><span v-if="result?.heart_rate?.hrv_indices?.NN50 != null"
                >NN50 <b>{{ result.heart_rate.hrv_indices.NN50 }}</b></span
              ><span v-if="result?.heart_rate?.hrv_indices?.pNN50 != null"
                >pNN50 <b>{{ result.heart_rate.hrv_indices.pNN50 }}</b
                >%</span
              >
            </div>
            <div class="metric-track">
              <i
                v-if="result?.heart_rate?.hrv_indices?.SDNN != null"
                :style="{
                  width: `${Math.max(0, Math.min(100, Number(result.heart_rate.hrv_indices.SDNN) / 1.2))}%`,
                }"
              ></i>
            </div>
          </section>
        </section>

        <aside class="insight-column">
          <section class="tech-panel score-panel">
            <div class="panel-heading">综合疲劳评分</div>
            <div
              class="score-ring"
              :class="{ ready: result?.physiological?.fatigue_probability != null }"
            >
              <div v-if="result?.physiological?.fatigue_probability != null">
                <strong>{{
                  Math.round((1 - result.physiological.fatigue_probability) * 100)
                }}</strong
                ><small>分</small>
              </div>
            </div>
            <div v-if="result?.physiological?.fatigue_probability != null" class="stars">★★★★★</div>
            <b
              v-if="result?.physiological?.fatigue_probability != null"
              :class="{ warning: result.physiological?.status === 'fatigue' }"
              >{{ result.physiological?.status === 'fatigue' ? '需要休息' : '状态良好' }}</b
            >
          </section>
          <section class="tech-panel advice-panel">
            <div class="panel-heading">AI 健康建议</div>
            <div class="advice">
              <span>AI</span>
              <div>
                <strong>{{ result?.advice?.level || '等待分析' }}</strong>
                <p>
                  {{
                    result?.advice?.text ||
                    '完成一分钟疲劳检测后，将根据面部行为特征和心率变异性生成建议。'
                  }}
                </p>
              </div>
            </div>
          </section>
        </aside>
      </div>

      <section class="tech-panel process-flow">
        <div class="flow-step" :class="{ active: recording || result }">
          <span>▣</span>
          <div><strong>高清摄像头接入</strong><small>硬件摄像头采集</small></div>
          <i>››</i>
        </div>
        <div class="flow-step" :class="{ active: recording || result }">
          <span>◎</span>
          <div><strong>AI 人体检测</strong><small>眨眼 · 哈欠 · 点头</small></div>
          <i>››</i>
        </div>
        <div class="flow-step" :class="{ active: result }">
          <span>⌁</span>
          <div><strong>HRV 数据分析</strong><small>KDE-HRV 指标计算</small></div>
          <i>››</i>
        </div>
        <div class="flow-step" :class="{ active: result }">
          <span>▤</span>
          <div><strong>智能评估与建议</strong><small>综合疲劳报告</small></div>
        </div>
      </section>
    </div>
  </DetectionShell>
</template>

<style scoped src="../../styles/detection/fatigue.css"></style>
