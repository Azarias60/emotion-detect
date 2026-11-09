import assert from 'node:assert/strict'
import test from 'node:test'
import { effectScope } from 'vue'
import { detectionPayload } from '../src/api/detectionPayload.js'
import { createHttpClient, errorMessage } from '../src/api/http.js'
import {
  heartRatePoints,
  audioWavePoints,
  physiologicalMetrics,
  fatigueMetrics,
} from '../src/utils/detectionMetrics.js'
import { useHardwareCapture } from '../src/composables/useHardwareCapture.js'
import { useHardwareMonitor } from '../src/composables/useHardwareMonitor.js'
import { useDetectionTask } from '../src/composables/useDetectionTask.js'
import { useCountdown } from '../src/composables/useCountdown.js'
import { detectionClient } from '../src/api/http.js'
import { useEmotionDetection } from '../src/features/detection/useEmotionDetection.js'
import { useHealthDetection } from '../src/features/detection/useHealthDetection.js'
import { useFatigueDetection } from '../src/features/detection/useFatigueDetection.js'

function deferred() {
  let resolve
  let reject
  const promise = new Promise((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}

test('hardware payload contains no synthetic files; health JSON remains structured', () => {
  const form = detectionPayload({
    tongue: { color: 'red' },
    questions: ['睡眠如何？'],
    answers: [{ question: '睡眠如何？', answer: '良好' }],
  })
  assert.equal(form.get('capture_source'), 'hardware')
  assert.equal(form.has('video'), false)
  assert.equal(form.has('audio'), false)
  assert.deepEqual(JSON.parse(form.get('answers')), [{ question: '睡眠如何？', answer: '良好' }])
  assert.throws(() => detectionPayload({ video: new Blob(['placeholder']) }), /仅支持后端硬件采集/)
  assert.throws(() => detectionPayload({ captureSource: 'browser' }), /仅支持后端硬件采集/)
})

test('fatigue uses hardware start, stop and analysis without uploading media', async (context) => {
  const requests = []
  const oldAdapter = detectionClient.defaults.adapter
  context.after(() => {
    detectionClient.defaults.adapter = oldAdapter
  })
  detectionClient.defaults.adapter = async (config) => {
    requests.push(config)
    return {
      config,
      status: 200,
      data: {
        code: 0,
        data: config.url === '/fatigue/detect' ? { physiological: { blink_count: 5 } } : {},
      },
    }
  }
  const scope = effectScope()
  context.after(() => scope.stop())
  const fatigue = scope.run(() => useFatigueDetection())
  await fatigue.start()
  assert.equal(fatigue.recording.value, true)
  assert.equal(fatigue.phase.value, 'capturing')
  await fatigue.stop()
  assert.equal(fatigue.phase.value, 'complete')
  assert.equal(fatigue.result.value.physiological.blink_count, 5)
  assert.deepEqual(
    requests.map((request) => request.url),
    ['/hardware/start', '/hardware/stop', '/fatigue/detect']
  )
  assert.equal(requests[2].data.get('capture_source'), 'hardware')
  assert.equal(requests[2].data.has('video'), false)
  assert.equal(requests[2].data.has('audio'), false)
})

test('both backend response dialects and validation arrays produce readable errors', () => {
  assert.equal(errorMessage({ msg: '硬件异常' }), '硬件异常')
  assert.equal(errorMessage({ message: '认证失败' }), '认证失败')
  assert.equal(
    errorMessage({ response: { data: { detail: [{ msg: '端口超出范围' }] } } }),
    '端口超出范围'
  )
})

test('HTTP client rejects business errors and preserves the status for auth handling', async () => {
  const errors = []
  const client = createHttpClient({ onError: (error) => errors.push(error) })
  client.defaults.adapter = async (config) => ({
    config,
    status: 200,
    data: { code: 1, message: '失败' },
  })
  await assert.rejects(client.get('/test'), /失败/)
  assert.equal(errors[0].response.status, 200)
  client.defaults.adapter = async (config) => ({
    config,
    status: 200,
    data: { code: 0, data: { count: 1 } },
  })
  assert.deepEqual((await client.get('/test')).data, { count: 1 })
})

test('missing metrics stay missing, zero temperature is valid, and no ECG is invented', () => {
  assert.equal(physiologicalMetrics().heartRate.value, '--')
  assert.equal(
    physiologicalMetrics({ temperature: { avg_ambient_temp: 0 } }).temperature.ambient,
    '0.0'
  )
  assert.equal(fatigueMetrics({})[0].value, null)
  assert.equal(heartRatePoints([]), '')
  assert.equal(heartRatePoints([72]), '64 25')
  assert.equal(heartRatePoints([70, 75]).split(' ').length, 4)
})

test('hardware charts preserve signed audio, silence and valid BPM samples', () => {
  assert.equal(audioWavePoints([]), '')
  assert.equal(audioWavePoints([-24, 0, 24]), '0 49 64 25 128 1')
  assert.equal(audioWavePoints([0, 0]), '0 25 128 25')
  assert.equal(audioWavePoints([null, 12]), '')
  assert.equal(audioWavePoints([NaN, 12]), '')
  assert.equal(audioWavePoints(null), '')
  assert.equal(heartRatePoints([0, null, Infinity, 231]), '')
  assert.equal(heartRatePoints(null), '')
  assert.equal(heartRatePoints([0, 72, 80, NaN]), heartRatePoints([72, 80]))
})

test('live physiology never falls back to recorded averages when a sensor is missing', () => {
  const data = {
    heart_rate: { latest_hr: null, avg_hr: 80 },
    temperature: { latest_ambient_temp: null, avg_ambient_temp: 25, avg_object_temp: 36 },
  }
  assert.equal(physiologicalMetrics(data, true).heartRate.value, '--')
  assert.equal(physiologicalMetrics(data, true).temperature.ambient, '--')
  assert.equal(physiologicalMetrics(data, true).temperature.object, '--')
  assert.equal(physiologicalMetrics(data).heartRate.value, '80.0')
})

test('hardware monitoring starts without recording, retries serially and cancels on disposal', async (context) => {
  const scope = effectScope()
  context.after(() => scope.stop())
  let nextPoll
  let schedules = 0
  context.mock.method(globalThis, 'setTimeout', (callback) => {
    nextPoll = callback
    schedules++
    return 1
  })
  context.mock.method(globalThis, 'clearTimeout', () => {})
  const requests = []
  const pending = deferred()
  const snapshot = { is_connected: true, heart_rate: { latest_hr: 72, live_hr_list: [72] } }
  const monitor = scope.run(() =>
    useHardwareMonitor({
      api: {
        captureData: (config) => {
          requests.push(config)
          if (requests.length === 1) return Promise.resolve(snapshot)
          if (requests.length === 2) return Promise.reject(new Error('offline'))
          if (requests.length === 3) return Promise.resolve({ ...snapshot, is_connected: false })
          if (requests.length === 4) return Promise.resolve(snapshot)
          return pending.promise
        },
      },
    })
  )
  assert.equal(requests.length, 1)
  await Promise.resolve()
  assert.deepEqual(monitor.liveData.value, snapshot)
  await nextPoll()
  assert.equal(monitor.liveData.value, null)
  await nextPoll()
  assert.equal(monitor.liveData.value, null)
  await nextPoll()
  assert.deepEqual(monitor.liveData.value, snapshot)
  const lastPoll = nextPoll()
  assert.equal(schedules, 4)
  scope.stop()
  assert.equal(requests.at(-1).signal.aborted, true)
  pending.resolve({ heart_rate: { latest_hr: 100 } })
  await lastPoll
  assert.equal(schedules, 4)
  assert.deepEqual(monitor.liveData.value, snapshot)
})

test('legacy hardware readings form a live trend without using stale recorded history', async (context) => {
  const scope = effectScope()
  context.after(() => scope.stop())
  let nextPoll
  context.mock.method(globalThis, 'setTimeout', (callback) => {
    nextPoll = callback
    return 1
  })
  context.mock.method(globalThis, 'clearTimeout', () => {})
  let payload = { is_connected: true, heart_rate: { latest_hr: 80, hr_list: [60, 60] } }
  const monitor = scope.run(() =>
    useHardwareMonitor({
      api: { captureData: async () => payload },
    })
  )
  await Promise.resolve()
  assert.deepEqual(monitor.liveData.value.heart_rate.live_hr_list, [80])
  assert.equal(heartRatePoints(monitor.liveData.value.heart_rate.live_hr_list), '64 25')
  await nextPoll()
  assert.deepEqual(monitor.liveData.value.heart_rate.live_hr_list, [80, 80])
  assert.notEqual(heartRatePoints(monitor.liveData.value.heart_rate.live_hr_list), '')
  assert.equal(payload.heart_rate.live_hr_list, undefined)
  payload.heart_rate.latest_hr = 85
  await nextPoll()
  assert.deepEqual(monitor.liveData.value.heart_rate.live_hr_list, [80, 80, 85])
  for (let i = 0; i < 130; i++) await nextPoll()
  assert.equal(monitor.liveData.value.heart_rate.live_hr_list.length, 128)
  payload.heart_rate.latest_hr = null
  await nextPoll()
  assert.ok(monitor.liveData.value.heart_rate.live_hr_list.every((value) => value === 85))
  payload = { is_connected: false }
  await nextPoll()
  assert.equal(monitor.liveData.value, null)
  payload = { is_connected: true, heart_rate: { latest_hr: 75 } }
  await nextPoll()
  assert.deepEqual(monitor.liveData.value.heart_rate.live_hr_list, [75])
  payload.heart_rate.live_hr_list = [71, 73, 75]
  await nextPoll()
  assert.deepEqual(monitor.liveData.value.heart_rate.live_hr_list, [71, 73, 75])
  await nextPoll()
  assert.deepEqual(monitor.liveData.value.heart_rate.live_hr_list, [71, 73, 75])
  payload.heart_rate.live_hr_list = []
  await nextPoll()
  assert.deepEqual(monitor.liveData.value.heart_rate.live_hr_list, [])
})

test('hardware capture retains the final charts and clears them for the next session', async (context) => {
  const scope = effectScope()
  context.after(() => scope.stop())
  const finalSnapshot = {
    audio: { recorded_wave: [-12, 0, 12] },
    heart_rate: { hr_list: [72, 75] },
  }
  let stopped = false
  const pending = deferred()
  const capture = scope.run(() =>
    useHardwareCapture({
      poll: true,
      api: {
        startCapture: async () => {
          stopped = false
        },
        stopCapture: async () => {
          stopped = true
        },
        captureData: () => (stopped ? Promise.resolve(finalSnapshot) : pending.promise),
      },
    })
  )
  await capture.start()
  await capture.stop()
  assert.deepEqual(capture.liveData.value, finalSnapshot)
  await capture.start()
  assert.equal(capture.liveData.value, null)
  pending.resolve({ audio: { recorded_wave: [] }, heart_rate: { hr_list: [] } })
})

test('hardware start completing after scope disposal is followed by stop', async () => {
  const scope = effectScope()
  const pending = deferred()
  let stops = 0
  const capture = scope.run(() =>
    useHardwareCapture({
      api: {
        startCapture: () => pending.promise,
        stopCapture: async () => {
          stops++
        },
      },
    })
  )
  const start = capture.start()
  scope.stop()
  pending.resolve()
  assert.equal(await start, false)
  assert.equal(stops, 1)
  assert.equal(capture.recording.value, false)
})

test('failed hardware start never marks the session recording', async () => {
  const scope = effectScope()
  const capture = scope.run(() =>
    useHardwareCapture({
      api: {
        startCapture: async () => {
          throw new Error('offline')
        },
        stopCapture: async () => {},
      },
    })
  )
  await assert.rejects(capture.start(), /offline/)
  assert.equal(capture.recording.value, false)
  scope.stop()
})

test('hardware stop is idempotent while a stop request is pending', async () => {
  const scope = effectScope()
  const pending = deferred()
  let stops = 0
  const capture = scope.run(() =>
    useHardwareCapture({
      api: {
        startCapture: async () => {},
        stopCapture: () => {
          stops++
          return pending.promise
        },
      },
    })
  )
  await capture.start()
  const first = capture.stop()
  const second = capture.stop()
  pending.resolve({ recorded_frames: 5 })
  assert.deepEqual(await first, await second)
  assert.equal(stops, 1)
  scope.stop()
})

test('task rejects duplicate work and suppresses late results after disposal', async () => {
  const scope = effectScope()
  const pending = deferred()
  const task = scope.run(() => useDetectionTask())
  let signal
  const first = task.run((value) => {
    signal = value
    return pending.promise
  })
  assert.equal(task.busy.value, true)
  await task.run(() => assert.fail('duplicate request'))
  scope.stop()
  assert.equal(signal.aborted, true)
  pending.resolve({ report: 'late result' })
  assert.equal(await first, undefined)
  assert.notEqual(task.phase.value, 'complete')
})

test('task failure is retryable and clears progress', async () => {
  const scope = effectScope()
  const task = scope.run(() => useDetectionTask())
  await task.run(async () => {
    throw new Error('network down')
  })
  assert.equal(task.errorMessage.value, 'network down')
  assert.equal(task.busy.value, false)
  assert.equal(task.analysisProgress.value, 0)
  assert.equal(await task.run(async () => 42, { showProgress: false }), 42)
  assert.equal(task.phase.value, 'complete')
  scope.stop()
})

test('countdown completes once and clears its timer on disposal', (context) => {
  let tick
  let now = 0
  let cleared = 0
  context.mock.method(globalThis, 'setInterval', (callback) => {
    tick = callback
    return 1
  })
  context.mock.method(globalThis, 'clearInterval', () => {
    cleared++
  })
  context.mock.method(Date, 'now', () => now)
  const scope = effectScope()
  const clock = scope.run(() => useCountdown(60))
  let complete = 0
  clock.start(() => {
    complete++
  })
  now = 20000
  tick()
  assert.equal(clock.remaining.value, 40)
  now = 60000
  tick()
  assert.equal(complete, 1)
  scope.stop()
  assert.ok(cleared >= 2)
})

test('failed hardware stop is retried before starting another recording', async () => {
  const scope = effectScope()
  let starts = 0
  let stops = 0
  const capture = scope.run(() =>
    useHardwareCapture({
      api: {
        startCapture: async () => {
          starts++
        },
        stopCapture: async () => {
          stops++
          if (stops === 1) throw new Error('offline')
        },
      },
    })
  )
  await capture.start()
  await assert.rejects(capture.stop(), /offline/)
  assert.equal(capture.recording.value, false)
  await capture.start()
  assert.equal(stops, 2)
  assert.equal(starts, 2)
  scope.stop()
})

test('stopping hardware prevents an in-flight poll from updating or scheduling another poll', async (context) => {
  const scope = effectScope()
  const polling = deferred()
  const stopping = deferred()
  let timers = 0
  context.mock.method(globalThis, 'setTimeout', () => {
    timers++
    return 1
  })
  const capture = scope.run(() =>
    useHardwareCapture({
      poll: true,
      api: {
        startCapture: async () => {},
        captureData: () => polling.promise,
        stopCapture: () => stopping.promise,
      },
    })
  )
  await capture.start()
  const stop = capture.stop()
  polling.resolve({ heart_rate: { avg_hr: 99 } })
  await Promise.resolve()
  assert.equal(capture.liveData.value, null)
  assert.equal(timers, 0)
  stopping.resolve()
  await stop
  scope.stop()
})

test('health workflow submits the same tongue/questions/answers contract as the backend', async (context) => {
  const requests = []
  const oldAdapter = detectionClient.defaults.adapter
  context.after(() => {
    detectionClient.defaults.adapter = oldAdapter
  })
  detectionClient.defaults.adapter = async (config) => {
    requests.push(config)
    const data =
      {
        '/health/tongue/capture': { tongue: { color: { class: 'red' } } },
        '/health/questions': ['睡眠如何？'],
        '/health/detect': { result: '健康报告', heart_rate: { avg_hr: 72 } },
      }[config.url] || {}
    return { config, status: 200, data: { code: 0, data } }
  }
  const scope = effectScope()
  context.after(() => scope.stop())
  const health = scope.run(() => useHealthDetection())
  await health.captureTongue()
  assert.equal(health.phase.value, 'ready')
  await health.startConsultation()
  assert.equal(health.phase.value, 'consulting')
  await health.stopConsultation()
  assert.equal(health.phase.value, 'complete')
  assert.equal(health.result.value.result, '健康报告')
  const form = requests.find((request) => request.url === '/health/detect').data
  assert.equal(form.get('capture_source'), 'hardware')
  assert.deepEqual(JSON.parse(form.get('questions')), ['睡眠如何？'])
  assert.equal(form.has('answers'), false)
  assert.equal(form.has('video'), false)
})

test('emotion flow finishes hardware capture before submitting a file-free analysis request', async (context) => {
  let now = 0
  let countdownTick
  const originalInterval = globalThis.setInterval
  context.mock.method(Date, 'now', () => now)
  context.mock.method(globalThis, 'setInterval', (callback, milliseconds) => {
    if (milliseconds === 250) countdownTick = callback
    return originalInterval(callback, milliseconds)
  })
  const requests = []
  let liveHeartRate = 72
  const oldAdapter = detectionClient.defaults.adapter
  context.after(() => {
    detectionClient.defaults.adapter = oldAdapter
  })
  detectionClient.defaults.adapter = async (config) => {
    requests.push(config)
    const data =
      config.url === '/emotion/questions'
        ? ['最近心情如何？']
        : config.url === '/emotion/detect'
          ? { result: '情绪报告', total_frames: 0 }
          : config.url === '/hardware/data'
            ? { is_connected: true, heart_rate: { latest_hr: liveHeartRate } }
            : {}
    return { config, status: 200, data: { code: 0, data } }
  }
  const scope = effectScope()
  context.after(() => scope.stop())
  const emotion = scope.run(() => useEmotionDetection())
  await new Promise((resolve) => setImmediate(resolve))
  assert.deepEqual(
    requests.map((request) => request.url),
    ['/hardware/data']
  )
  assert.equal(emotion.liveData.value.heart_rate.latest_hr, 72)
  await emotion.start()
  assert.equal(emotion.phase.value, 'capturing')
  now = 60000
  countdownTick()
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(emotion.hasRecording.value, true)
  await emotion.analyze()
  assert.equal(emotion.phase.value, 'complete')
  assert.equal(emotion.result.value.result, '情绪报告')
  liveHeartRate = 85
  await new Promise((resolve) => setTimeout(resolve, 300))
  assert.equal(emotion.liveData.value.heart_rate.latest_hr, 85)
  const form = requests.find((request) => request.url === '/emotion/detect').data
  assert.equal(form.get('capture_source'), 'hardware')
  assert.equal(form.has('audio'), false)
  assert.equal(form.has('video'), false)
  assert.ok(
    requests.findIndex((request) => request.url === '/hardware/stop') <
      requests.findIndex((request) => request.url === '/emotion/detect')
  )
})
