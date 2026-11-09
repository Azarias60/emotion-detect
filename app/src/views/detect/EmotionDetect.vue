<script setup>
import DetectionNav from '@/components/DetectionNav.vue'
import DetectionProcess from '@/components/DetectionProcess.vue'
import { useDetectionGuard } from '@/composables/useDetectionGuard'
import { useEmotionDetection } from '@/features/detection/useEmotionDetection'
import { useEmotionPresentation } from '@/features/detection/useEmotionPresentation'
import { videoFeedUrl } from '@/api/http'
import heartIcon from '@/assets/heart.png'
import breathIcon from '@/assets/breath.png'
import ambientTempIcon from '@/assets/ambientTemp.png'
import bodyTempIcon from '@/assets/bodyTemp.png'
import hrvIcon from '@/assets/hrv.png'

const {
  phase,
  busy,
  result,
  liveData,
  hasRecording,
  isRecording,
  isAnalyzing,
  countdown,
  displayProgress,
  currentQuestion,
  reportDialogVisible,
  errorMessage,
  start,
  analyze: handleVideoDetect,
  viewReport: handleViewReport,
} = useEmotionDetection()
useDetectionGuard(busy)
const {
  emotions,
  emotionWordCount,
  physiologicalData,
  behaviorData,
  voiceWave,
  heartPoints,
  reportParagraphs,
} = useEmotionPresentation(result, liveData)
function handleStartFlow() {
  if (busy.value) return
  if (
    window.confirm(
      '本次情绪识别将持续约 1 分钟，单片机将采集视频、音频、体温与心率。检测过程中请勿切换页面。是否开始？'
    )
  )
    start()
}
</script>

<template>
  <div class="fui-screen">
    <DetectionProcess
      :phase="phase"
      :progress="displayProgress"
      :remaining="countdown"
      :message="phase === 'capturing' ? '请保持面部在取景框中，并自然回答当前问题' : ''"
    />
    <header class="fui-header">
      <div class="header-main-title">情绪识别分析系统</div>
      <DetectionNav />
    </header>

    <div class="fui-container">
      <aside class="side-col">
        <section class="info-panel emotion-word-panel">
          <div class="panel-title">喜怒哀虑词汇统计</div>
          <div class="emotion-word-grid">
            <div v-for="item in emotionWordCount" :key="item.label" class="emotion-word-item">
              <div class="octagon-box">
                <div class="octagon-inner">
                  <img :src="item.iconUrl" class="emotion-icon-img" alt="icon" />
                </div>
              </div>
              <div class="emotion-word-info">
                <div class="emotion-word-label">{{ item.label }}</div>
                <div class="emotion-word-value">
                  <span class="ew-val">{{ item.count }}</span>
                  <span class="ew-unit">{{ item.unit }}</span>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section class="info-panel bottom-half">
          <div class="panel-title">微表情识别权重</div>
          <div class="emotion-grid">
            <div v-for="item in emotions" :key="item.key" class="emotion-card">
              <div class="emo-icon">{{ item.icon }}</div>
              <span class="emo-label">{{ item.label }}</span>
              <div class="emo-bar-bg">
                <div class="emo-bar-fill" :style="{ width: item.val + '%' }"></div>
              </div>
              <span class="emo-val">{{ item.val }}%</span>
            </div>
          </div>
        </section>
      </aside>

      <main class="center-col">
        <div class="video-viewport">
          <div class="v-corner tl"></div>
          <div class="v-corner tr"></div>
          <div class="v-corner bl"></div>
          <div class="v-corner br"></div>
          <div class="video-canvas">
            <img
              :src="videoFeedUrl"
              alt="ESP32 摄像头画面"
              style="width: 100%; height: 100%; object-fit: cover"
            />
            <div v-if="isRecording" class="scan-anim"></div>
          </div>
        </div>

        <div class="orange-status-box">
          <div class="status-content">
            <span class="blink-text">> </span>
            <span v-if="errorMessage">{{ errorMessage }}</span>
            <span v-else-if="isRecording">{{ currentQuestion }}</span>
            <span v-else-if="isAnalyzing">正在分析数据，请稍候...</span>
            <span v-else-if="hasRecording">录制已完成，可点击分析按钮。</span>
            <span v-else>请点击开始录制按钮启动检测流程</span>
          </div>
        </div>

        <div class="action-bar">
          <div class="fui-btn btn-cyan" @click="handleStartFlow" :class="{ disabled: busy }">
            <div class="btn-inner">开始录制</div>
          </div>
          <div
            class="fui-btn btn-blue"
            @click="handleVideoDetect"
            :class="{ disabled: !hasRecording || busy }"
          >
            <div class="btn-inner">
              <span v-if="isAnalyzing">分析中...</span>
              <span v-else>开始分析</span>
            </div>
          </div>
          <div
            class="fui-btn btn-gold"
            @click="handleViewReport"
            :class="{ disabled: !hasRecording || busy }"
          >
            <div class="btn-inner">查看分析结果</div>
          </div>
        </div>
      </main>

      <aside class="side-col right-panel">
        <section class="info-panel wave-panel">
          <div class="panel-title">波形监测</div>
          <div class="wave-container">
            <div class="wave-item">
              <div class="wave-label">语音波形</div>
              <div class="wave-canvas line-wave">
                <span v-if="!voiceWave" class="wave-empty">等待硬件音频</span>
                <svg
                  v-else
                  viewBox="0 0 128 50"
                  preserveAspectRatio="none"
                  role="img"
                  aria-label="硬件实时语音波形"
                >
                  <polyline :points="voiceWave" fill="none" stroke="#00f2ff" stroke-width="1" />
                </svg>
              </div>
            </div>
            <div class="wave-item">
              <div class="wave-label">心率历史趋势</div>
              <div class="wave-canvas line-wave">
                <span v-if="!heartPoints" class="wave-empty">等待硬件心率</span>
                <svg
                  v-if="heartPoints"
                  width="100%"
                  height="100%"
                  viewBox="0 0 128 50"
                  preserveAspectRatio="none"
                  role="img"
                  aria-label="硬件实时心率历史趋势"
                >
                  <circle v-if="heartPoints === '64 25'" cx="64" cy="25" r="2" fill="#00f2ff" />
                  <polyline
                    v-else
                    :points="heartPoints"
                    fill="none"
                    stroke="#00f2ff"
                    stroke-width="1.5"
                  />
                </svg>
              </div>
            </div>
          </div>
        </section>

        <section class="info-panel physiological-panel">
          <div class="panel-title">生理指标分析</div>
          <div class="physiological-grid">
            <div class="physiological-item">
              <div class="img-placeholder small-placeholder">
                <img :src="heartIcon" class="physio-icon" alt="心率" />
              </div>
              <div class="physio-content">
                <div class="physio-label">心率</div>
                <div class="physio-value">
                  {{ physiologicalData.heartRate.value }} {{ physiologicalData.heartRate.unit }}
                </div>
              </div>
            </div>
            <div class="physiological-item">
              <div class="img-placeholder small-placeholder"><span class="bp-symbol">BP</span></div>
              <div class="physio-content">
                <div class="physio-label">血压</div>
                <div class="physio-value">
                  {{ physiologicalData.bloodPressure.systolic }}/{{
                    physiologicalData.bloodPressure.diastolic
                  }}
                  mmHg
                </div>
              </div>
            </div>
            <div class="physiological-item">
              <div class="img-placeholder small-placeholder">
                <img :src="breathIcon" class="physio-icon" alt="呼吸率" />
              </div>
              <div class="physio-content">
                <div class="physio-label">呼吸率</div>
                <div class="physio-value">
                  {{ physiologicalData.respirationRate.value }}
                  {{ physiologicalData.respirationRate.unit }}
                </div>
              </div>
            </div>
            <div class="physiological-item">
              <div class="img-placeholder small-placeholder">
                <img :src="ambientTempIcon" class="physio-icon" alt="环境温度" />
              </div>
              <div class="physio-content">
                <div class="physio-label">环境温度</div>
                <div class="physio-value">
                  {{ physiologicalData.temperature.ambient }}
                  {{ physiologicalData.temperature.unit }}
                </div>
              </div>
            </div>
            <div class="physiological-item">
              <div class="img-placeholder small-placeholder">
                <img :src="bodyTempIcon" class="physio-icon" alt="体表温度" />
              </div>
              <div class="physio-content">
                <div class="physio-label">体表温度</div>
                <div class="physio-value">
                  {{ physiologicalData.temperature.object }}
                  {{ physiologicalData.temperature.unit }}
                </div>
              </div>
            </div>
            <div class="physiological-item hrv-item">
              <div class="img-placeholder small-placeholder">
                <img :src="hrvIcon" class="physio-icon" alt="心率变异指数" />
              </div>
              <div class="physio-content">
                <div class="physio-label">心率变异指数</div>
                <div class="hrv-grid">
                  <div class="hrv-subitem">SDNN: {{ physiologicalData.hrvIndex.sdnn }}</div>
                  <div class="hrv-subitem">RMSSD: {{ physiologicalData.hrvIndex.rmssd }}</div>
                  <div class="hrv-subitem">PNN50: {{ physiologicalData.hrvIndex.pnn50 }}%</div>
                  <div class="hrv-subitem">NN50: {{ physiologicalData.hrvIndex.nn50 }}</div>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section class="info-panel behavior-panel">
          <div class="panel-title">行为特征提取</div>
          <div class="behavior-container">
            <div
              v-for="item in behaviorData"
              :key="item.label"
              class="behavior-item"
              :class="{ 'status-warn': item.val === '疲劳' }"
            >
              <div class="octagon-box">
                <div class="octagon-inner">
                  <img :src="item.iconUrl" class="mini-img" />
                </div>
              </div>
              <div class="behavior-info">
                <div class="behavior-label">{{ item.label }}</div>
                <div class="behavior-value">
                  <span class="b-val">{{ item.val }}</span>
                  <span class="b-unit">{{ item.unit }}</span>
                </div>
              </div>
            </div>
          </div>
        </section>
      </aside>
    </div>

    <div class="report-dialog-mask" v-if="reportDialogVisible" @click="reportDialogVisible = false">
      <div class="report-dialog" @click.stop>
        <div class="report-dialog-header">
          <div class="dialog-title">多模态情感分析报告</div>
          <div class="dialog-close" @click="reportDialogVisible = false">×</div>
        </div>
        <div class="report-dialog-body">
          <div class="report-content">
            <p
              v-for="(paragraph, index) in reportParagraphs"
              :key="index"
              class="report-para"
              style="white-space: pre-line"
            >
              {{ paragraph }}
            </p>
          </div>
        </div>
        <div class="report-dialog-footer">
          <div class="fui-btn btn-cyan dialog-btn" @click="reportDialogVisible = false">
            <div class="btn-inner">关闭</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped src="../../styles/detection/emotion.css"></style>
