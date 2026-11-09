<script setup>
import { computed } from 'vue'
import DetectionShell from '@/components/DetectionShell.vue'
import DetectionProcess from '@/components/DetectionProcess.vue'
import { useDetectionGuard } from '@/composables/useDetectionGuard'
import { useHealthDetection } from '@/features/detection/useHealthDetection'
import { videoFeedUrl } from '@/api/http'
import { hasValue, scalePercent } from '@/utils/detectionMetrics'

const {
  phase,
  busy,
  tongueResult,
  result,
  isTongueDetecting,
  countdown,
  displayProgress,
  questions,
  currentQuestionIndex,
  errorMessage,
  captureTongue,
  startConsultation: start,
  stopConsultation: stop,
  resetConsultation,
} = useHealthDetection()
useDetectionGuard(busy)
function startConsultation() {
  if (
    window.confirm(
      '本次健康问诊将持续约 1 分钟。单片机将同步采集视频、音频、体温与心率。是否开始？'
    )
  )
    start()
}
function requestStopConsultation() {
  if (window.confirm('提前结束可能导致综合报告不准确。确定结束并分析当前数据吗？')) stop()
}
const tongueRows = computed(() => {
  const tongue = tongueResult.value?.tongue || {}
  return [
    ['舌色', tongue.color],
    ['苔色', tongue.coated],
    ['裂纹', tongue.crack],
    ['齿痕', tongue.indentation],
  ].filter(([, item]) => item)
})
const moduleItems = [
  { key: 'tongue', icon: '◈', title: '舌苔检测', desc: '先截图，再进入健康问诊' },
  { key: 'heart', icon: '♥', title: '心率检测', desc: '摄像头 KDE-HRV 分析' },
  { key: 'bp', icon: 'BP', title: '血压', desc: 'KDE-HRV 模型分析' },
  { key: 'breath', icon: '≈', title: '呼吸率', desc: '后续接入', pending: true },
  { key: 'ai', icon: 'AI', title: 'AI 问答', desc: '由舌象结果生成问题' },
]
</script>

<template>
  <DetectionShell title="健康问诊" subtitle="TONGUE CAPTURE · ONE-MINUTE CONSULTATION">
    <DetectionProcess
      :phase="phase"
      :progress="displayProgress"
      :remaining="countdown"
      :message="phase === 'consulting' ? '请保持面部位于取景框内，并自然回答当前问题' : ''"
    />
    <div class="health-dashboard">
      <div class="dashboard-main">
        <section class="tech-panel camera-panel sensor-panel">
          <div class="panel-heading live-heading">
            <span class="live-dot"></span
            >{{
              phase === 'tongue' || phase === 'tongue-capturing'
                ? '实时画面 · 舌象取景'
                : '实时画面 · 健康问诊'
            }}<small>非接触式检测</small>
          </div>
          <div class="camera-frame">
            <img
              :src="videoFeedUrl"
              alt="ESP32 Stream"
              style="width: 100%; height: 100%; object-fit: cover"
            />
            <div class="camera-corners"><i></i><i></i><i></i><i></i></div>
            <div v-if="phase === 'tongue' || phase === 'tongue-capturing'" class="tongue-guide">
              <div class="guide-shape"></div>
              <span>请伸出舌头并保持 2 秒</span>
            </div>
            <div v-else class="face-target"><span></span></div>
            <div class="scan-line"></div>
            <div class="camera-badge">
              <b>AI</b
              ><span>{{
                phase === 'consulting'
                  ? `问题 ${currentQuestionIndex + 1}/${questions.length}`
                  : '视觉健康检测'
              }}</span>
            </div>
          </div>
          <div class="capture-tip">
            {{
              phase === 'tongue' || phase === 'tongue-capturing'
                ? '请正面伸舌 · 光线均匀 · 不要使用滤镜'
                : phase === 'consulting'
                  ? `请回答：${questions[currentQuestionIndex] || '请自然回答'}`
                  : phase === 'analyzing'
                    ? '正在联合分析舌象、心率和问诊回答'
                    : phase === 'complete'
                      ? '综合报告已生成'
                      : '舌象截图完成后开始一分钟问诊'
            }}
          </div>
          <div class="actions">
            <button
              v-if="phase === 'tongue'"
              class="tech-button orange"
              :disabled="isTongueDetecting"
              @click="captureTongue"
            >
              {{ isTongueDetecting ? '截图分析中...' : '截图并分析舌象' }}</button
            ><button v-if="phase === 'ready'" class="tech-button" @click="startConsultation">
              开始一分钟问诊</button
            ><button
              v-if="phase === 'consulting'"
              class="tech-button orange"
              @click="requestStopConsultation"
            >
              提前结束并分析</button
            ><button v-if="phase === 'complete'" class="tech-button" @click="resetConsultation">
              重新问诊
            </button>
          </div>
          <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
        </section>

        <section class="tech-panel analysis-panel">
          <div class="panel-heading">健康分析结果 <small>HEALTH DATA ANALYSIS</small></div>
          <div class="analysis-grid">
            <article class="data-card">
              <div class="data-title"><span>♥</span>心率</div>
              <template v-if="hasValue(result?.heart_rate?.avg_hr)"
                ><strong>{{ result.heart_rate.avg_hr }} <em>bpm</em></strong>
                <div class="wave-line cyan"></div
              ></template>
              <div class="metric-track">
                <i
                  v-if="hasValue(result?.heart_rate?.avg_hr)"
                  :style="{ width: `${scalePercent(result.heart_rate.avg_hr, 40, 140)}%` }"
                ></i>
              </div>
            </article>
            <article class="data-card">
              <div class="data-title"><span>≈</span>呼吸率</div>
              <template
                v-if="hasValue(result?.respiratory_rate ?? result?.physiological?.respiratory_rate)"
                ><strong
                  >{{ result.respiratory_rate ?? result.physiological.respiratory_rate }}
                  <em>次/分</em></strong
                >
                <div class="wave-line blue"></div
              ></template>
              <div class="metric-track">
                <i
                  v-if="
                    hasValue(result?.respiratory_rate ?? result?.physiological?.respiratory_rate)
                  "
                  :style="{
                    width: `${scalePercent(result.respiratory_rate ?? result.physiological.respiratory_rate, 8, 30)}%`,
                  }"
                ></i>
              </div>
            </article>
            <article class="data-card">
              <div class="data-title"><span>BP</span>血压趋势</div>
              <template
                v-if="
                  hasValue(result?.heart_rate?.blood_pressure?.systolic) ||
                  hasValue(result?.heart_rate?.blood_pressure?.diastolic)
                "
                ><strong
                  v-if="
                    hasValue(result?.heart_rate?.blood_pressure?.systolic) ||
                    hasValue(result?.heart_rate?.blood_pressure?.diastolic)
                  "
                  ><span v-if="hasValue(result?.heart_rate?.blood_pressure?.systolic)">{{
                    result.heart_rate.blood_pressure.systolic
                  }}</span
                  ><span
                    v-if="
                      hasValue(result?.heart_rate?.blood_pressure?.systolic) &&
                      hasValue(result?.heart_rate?.blood_pressure?.diastolic)
                    "
                  >
                    / </span
                  ><span v-if="hasValue(result?.heart_rate?.blood_pressure?.diastolic)">{{
                    result.heart_rate.blood_pressure.diastolic
                  }}</span>
                  <em>mmHg</em></strong
                >
                <div class="wave-line orange"></div
              ></template>
              <div class="metric-track dual">
                <i
                  v-if="hasValue(result?.heart_rate?.blood_pressure?.systolic)"
                  :style="{
                    width: `${scalePercent(result.heart_rate.blood_pressure.systolic, 80, 180)}%`,
                  }"
                ></i
                ><i
                  v-if="hasValue(result?.heart_rate?.blood_pressure?.diastolic)"
                  class="secondary"
                  :style="{
                    width: `${scalePercent(result.heart_rate.blood_pressure.diastolic, 50, 120)}%`,
                  }"
                ></i>
              </div>
            </article>
            <article class="data-card">
              <div class="data-title"><span>HRV</span>心率变异性</div>
              <template v-if="result?.heart_rate?.hrv_indices"
                ><strong v-if="hasValue(result.heart_rate.hrv_indices.SDNN)"
                  >{{ result.heart_rate.hrv_indices.SDNN }} <em>ms</em></strong
                >
                <div class="mini-values">
                  <span v-if="hasValue(result.heart_rate.hrv_indices.RMSSD)"
                    >RMSSD {{ result.heart_rate.hrv_indices.RMSSD }}</span
                  ><span v-if="hasValue(result.heart_rate.hrv_indices.NN50)"
                    >NN50 {{ result.heart_rate.hrv_indices.NN50 }}</span
                  ><span v-if="hasValue(result.heart_rate.hrv_indices.pNN50)"
                    >pNN50 {{ result.heart_rate.hrv_indices.pNN50 }}%</span
                  >
                </div></template
              >
              <div class="metric-track">
                <i
                  v-if="hasValue(result?.heart_rate?.hrv_indices?.SDNN)"
                  :style="{ width: `${scalePercent(result.heart_rate.hrv_indices.SDNN, 0, 120)}%` }"
                ></i>
              </div>
            </article>
            <article class="data-card tongue-card">
              <div class="data-title"><span>◈</span>舌象识别</div>
              <template v-if="tongueRows.length"
                ><div class="tongue-tags">
                  <span v-for="[label, item] in tongueRows" :key="label"
                    >{{ label }} · <b>{{ item.label }}</b></span
                  >
                </div>
                <strong
                  v-if="tongueResult?.constitution_candidates?.[0]?.name"
                  class="constitution-name"
                  >{{ tongueResult.constitution_candidates[0].name }}</strong
                ></template
              >
              <div v-else class="card-placeholder"></div>
            </article>
            <article class="data-card question-card">
              <div class="data-title"><span>AI</span>关联问诊</div>
              <template v-if="questions.length"
                ><small>问题 {{ currentQuestionIndex + 1 }} / {{ questions.length }}</small>
                <p>{{ questions[currentQuestionIndex] }}</p>
                <em v-if="phase === 'consulting'">回答由硬件录音，结束后统一转写</em></template
              >
              <div v-else class="card-placeholder"></div>
            </article>
          </div>
        </section>

        <aside class="insight-column">
          <section class="tech-panel score-panel">
            <div class="panel-heading">综合健康评分</div>
            <div class="score-ring" :class="{ ready: result?.health_score != null }">
              <div v-if="result?.health_score != null">
                <strong>{{ result.health_score }}</strong
                ><small>分</small>
              </div>
            </div>
            <div v-if="result?.health_score != null" class="stars">★★★★★</div>
            <b v-if="result?.health_score != null">{{ result.health_level || '良好' }}</b>
          </section>
          <section class="tech-panel advice-panel">
            <div class="panel-heading">AI 健康建议</div>
            <div class="advice-body">
              <span>AI</span>
              <p v-if="result">{{ result.result }}</p>
              <p v-else-if="tongueResult?.report?.one_line">{{ tongueResult.report.one_line }}</p>
              <p v-else>完成舌象截图与一分钟问诊后，将在这里生成综合健康建议。</p>
            </div>
          </section>
        </aside>
      </div>

      <section class="tech-panel process-flow">
        <div
          v-for="(item, index) in moduleItems"
          :key="item.key"
          class="flow-step"
          :class="{
            active:
              (item.key === 'tongue' && tongueResult) || (item.key === 'ai' && questions.length),
            pending: item.pending,
          }"
        >
          <span>{{ item.icon }}</span>
          <div>
            <strong>{{ item.title }}</strong
            ><small>{{ item.pending ? '等待设备接入' : item.desc }}</small>
          </div>
          <i v-if="index < moduleItems.length - 1">››</i>
        </div>
      </section>
    </div>
  </DetectionShell>
</template>

<style scoped src="../../styles/detection/health.css"></style>
