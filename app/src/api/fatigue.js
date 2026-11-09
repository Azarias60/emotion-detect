import { detectionClient as http, responseData } from './http.js'
import { detectionPayload } from './detectionPayload.js'

export const fatigueDetect = (input, config) =>
  responseData(http.post('/fatigue/detect', detectionPayload(input), config))
