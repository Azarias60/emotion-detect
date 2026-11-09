import { detectionClient as http, responseData } from './http.js'

export const startCapture = (duration = 60, config) =>
  responseData(http.post('/hardware/start', { record_duration: duration }, config))
export const stopCapture = (config) => responseData(http.post('/hardware/stop', undefined, config))
export const captureData = (config) => responseData(http.get('/hardware/data', config))
export const getHardwareMode = (config) => responseData(http.get('/hardware/mode', config))
export const setHardwareMode = (payload, config) =>
  responseData(http.post('/hardware/mode', payload, config))
