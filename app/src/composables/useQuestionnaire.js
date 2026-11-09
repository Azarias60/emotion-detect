import { computed, onScopeDispose, ref } from 'vue'

export function useQuestionnaire() {
  const questions = ref([])
  const index = ref(0)
  const currentQuestion = computed(() => questions.value[index.value] || '')
  let timer
  let active = false
  let questionSeconds = 15
  function schedule(seconds) {
    clearTimeout(timer)
    timer = setTimeout(advance, seconds * 1000)
  }
  function advance() {
    if (!active || index.value >= questions.value.length - 1) return
    index.value += 1
    schedule(questionSeconds)
  }
  function stop() {
    active = false
    clearTimeout(timer)
  }
  function reset() {
    stop()
    index.value = 0
  }
  function setQuestions(items) {
    questions.value = Array.isArray(items) ? items.filter((item) => typeof item === 'string') : []
    reset()
  }
  function start({ interval = 15 } = {}) {
    reset()
    questionSeconds = interval
    active = true
    schedule(questionSeconds)
  }
  onScopeDispose(stop)
  return {
    questions,
    index,
    currentQuestion,
    setQuestions,
    start,
    stop,
    reset,
  }
}
