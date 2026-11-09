import { detectionClient as http, responseData } from './http.js'
import { detectionPayload } from './detectionPayload.js'

export const emotionQuestions = (config) => responseData(http.get('/emotion/questions', config))
export const emotionDetect = (input, config) =>
  responseData(http.post('/emotion/detect', detectionPayload(input), config))
