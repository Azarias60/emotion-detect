/** 检测仅引用后端硬件会话；前端只提交问诊上下文。 */
export function detectionPayload(input = {}) {
  const fields = { tongue: 'tongue_result', questions: 'questions', answers: 'answers' }
  if (Object.keys(input).some((key) => !(key in fields)))
    throw new Error('仅支持后端硬件采集，不接收采集来源或音视频文件')
  const form = new FormData()
  form.append('capture_source', 'hardware')
  for (const [key, field] of Object.entries(fields)) {
    if (input[key] !== undefined) form.append(field, JSON.stringify(input[key]))
  }
  return form
}
