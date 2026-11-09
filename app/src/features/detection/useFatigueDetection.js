import { computed, ref } from 'vue'
import { fatigueDetect } from '../../api/fatigue.js'
import { useDetectionTask } from '../../composables/useDetectionTask.js'
import { useCountdown } from '../../composables/useCountdown.js'
import { useHardwareCapture } from '../../composables/useHardwareCapture.js'

export function useFatigueDetection() {
  const result = ref(null)
  const task = useDetectionTask()
  const countdown = useCountdown()
  const capture = useHardwareCapture()
  const analyzing = computed(() => task.phase.value === 'analyzing')
  const displayProgress = computed(() =>
    analyzing.value ? task.analysisProgress.value : countdown.progress.value
  )

  async function start() {
    if (task.busy.value) return
    result.value = null
    countdown.reset()
    const started = await task.run(() => capture.start(), {
      pending: 'preparing',
      success: 'capturing',
      showProgress: false,
    })
    if (started) countdown.start(stop)
  }
  async function stop() {
    if (task.running.value || !capture.recording.value) return
    countdown.stop()
    const data = await task.run(async (signal) => {
      await capture.stop()
      return fatigueDetect({}, { signal })
    })
    if (data) result.value = data
  }
  return {
    ...task,
    remaining: countdown.remaining,
    displayProgress,
    result,
    recording: capture.recording,
    analyzing,
    start,
    stop,
  }
}
