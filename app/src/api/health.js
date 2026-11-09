import { detectionClient as http, responseData } from './http.js'
import { detectionPayload } from './detectionPayload.js'

export const tongueCapture = (config) =>
  responseData(http.post('/health/tongue/capture', undefined, config))
export const healthQuestions = (tongue, config) =>
  responseData(http.post('/health/questions', { tongue }, config))
export const healthDetect = (input, config) =>
  responseData(http.post('/health/detect', detectionPayload(input), config))
