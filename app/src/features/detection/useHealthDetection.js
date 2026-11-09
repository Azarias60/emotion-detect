import { computed, ref } from 'vue'
import * as healthApi from '../../api/health.js'
import { useDetectionTask } from '../../composables/useDetectionTask.js'
import { useCountdown } from '../../composables/useCountdown.js'
import { useHardwareCapture } from '../../composables/useHardwareCapture.js'
import { useQuestionnaire } from '../../composables/useQuestionnaire.js'

export function useHealthDetection() {
  const task = useDetectionTask('tongue')
  const capture = useHardwareCapture()
  const clock = useCountdown()
  const questionnaire = useQuestionnaire()
  const tongueResult = ref(null)
  const result = ref(null)
  const isTongueDetecting = computed(() => task.phase.value === 'tongue-analyzing')
  const isAnalyzing = computed(() => task.phase.value === 'analyzing')
  const displayProgress = computed(() =>
    isTongueDetecting.value || isAnalyzing.value
      ? task.analysisProgress.value
      : clock.progress.value
  )

  async function captureTongue() {
    if (task.busy.value) return
    result.value = null
    const data = await task.run(
      async (signal) => {
        const tongue = await healthApi.tongueCapture({ signal })
        const questions = await healthApi.healthQuestions(tongue, { signal })
        return { tongue, questions }
      },
      { pending: 'tongue-analyzing', success: 'ready', failure: 'tongue' }
    )
    if (data) {
      tongueResult.value = data.tongue
      questionnaire.setQuestions(data.questions)
    }
  }
  async function startConsultation() {
    if (task.busy.value || !tongueResult.value || !questionnaire.questions.value.length) return
    result.value = null
    clock.reset()
    const started = await task.run(() => capture.start(), {
      pending: 'preparing',
      success: 'consulting',
      failure: 'ready',
      showProgress: false,
    })
    if (started) {
      questionnaire.start({ interval: Math.max(8, 60 / questionnaire.questions.value.length) })
      clock.start(stopConsultation)
    }
  }
  async function stopConsultation() {
    if (task.running.value || !capture.recording.value) return
    clock.stop()
    questionnaire.stop()
    const data = await task.run(
      async (signal) => {
        await capture.stop()
        return healthApi.healthDetect(
          {
            tongue: tongueResult.value,
            questions: questionnaire.questions.value,
          },
          { signal }
        )
      },
      { failure: 'ready' }
    )
    if (data) result.value = data
  }
  function resetConsultation() {
    if (task.busy.value) return
    result.value = null
    task.phase.value = tongueResult.value ? 'ready' : 'tongue'
    clock.reset()
    questionnaire.reset()
  }
  return {
    ...task,
    tongueResult,
    result,
    isTongueDetecting,
    isAnalyzing,
    isRecording: capture.recording,
    countdown: clock.remaining,
    displayProgress,
    questions: questionnaire.questions,
    currentQuestionIndex: questionnaire.index,
    captureTongue,
    startConsultation,
    stopConsultation,
    resetConsultation,
  }
}
