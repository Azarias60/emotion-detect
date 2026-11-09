import request from '@/utils/request'
export const getUserInfo = () => request.get('/user/userInfo')
export const updateUser = (data) => request.put('/user/update', data)
export const updatePassword = (data) => request.put('/user/updatePwd', data)
export const uploadAvatar = (file) => {
  const form = new FormData()
  form.append('file', file)
  return request.post('/upload', form, { headers: { 'Content-Type': 'multipart/form-data' } })
}
