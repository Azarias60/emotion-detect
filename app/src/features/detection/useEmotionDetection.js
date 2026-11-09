import { computed, ref } from 'vue'
import * as emotionApi from '../../api/emotion.js'
import { useDetectionTask } from '../../composables/useDetectionTask.js'
import { useCountdown } from '../../composables/useCountdown.js'
import { useHardwareCapture } from '../../composables/useHardwareCapture.js'
import { useHardwareMonitor } from '../../composables/useHardwareMonitor.js'
import { useQuestionnaire } from '../../composables/useQuestionnaire.js'

const fallbackQuestions = [
  '最近心情怎么样？',
  '睡眠还好吗？',
  '最近压力大吗？',
  '有什么烦恼吗？',
  '饮食正常吗？',
  '工作顺利吗？',
  '社交多吗？',
  '运动吗？',
  '有爱好吗？',
  '对未来乐观吗？',
]

export function useEmotionDetection() {
  const task = useDetectionTask()
  const clock = useCountdown()
  const capture = useHardwareCapture()
  const monitor = useHardwareMonitor()
  const questionnaire = useQuestionnaire()
  const result = ref(null)
  const hasRecording = ref(false)
  const reportDialogVisible = ref(false)
  const isAnalyzing = computed(() => task.phase.value === 'analyzing')
  const displayProgress = computed(() =>
    isAnalyzing.value ? task.analysisProgress.value : clock.progress.value
  )

  async function start() {
    if (task.busy.value) return
    result.value = null
    hasRecording.value = false
    reportDialogVisible.value = false
    clock.reset()
    const started = await task.run(
      async (signal) => {
        let questions
        try {
          questions = await emotionApi.emotionQuestions({ signal })
        } catch (error) {
          if (signal.aborted) throw error
          questions = fallbackQuestions
        }
        if (signal.aborted) return false
        questionnaire.setQuestions(questions?.length ? questions : fallbackQuestions)
        return capture.start()
      },
      { pending: 'preparing', success: 'capturing', showProgress: false }
    )
    if (started) {
      questionnaire.start({ interval: 15 })
      clock.start(stop)
    }
  }
  async function stop() {
    clock.stop()
    questionnaire.stop()
    const stopped = await task.run(
      async () => {
        await capture.stop()
        return true
      },
      { pending: 'uploading', success: 'ready', showProgress: false }
    )
    if (stopped) hasRecording.value = true
  }
  async function analyze() {
    if (task.busy.value || !hasRecording.value) return
    const data = await task.run((signal) => emotionApi.emotionDetect({}, { signal }))
    if (data) result.value = data
  }
  async function viewReport() {
    if (!hasRecording.value || task.busy.value) return
    if (!result.value) await analyze()
    if (result.value) reportDialogVisible.value = true
  }
  return {
    ...task,
    result,
    hasRecording,
    reportDialogVisible,
    isAnalyzing,
    isRecording: capture.recording,
    countdown: clock.remaining,
    displayProgress,
    liveData: monitor.liveData,
    currentQuestion: questionnaire.currentQuestion,
    start,
    analyze,
    viewReport,
  }
}
