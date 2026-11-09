import request from '@/utils/request'
export const register = (data) => request.post('/user/register', data)
export const login = (data) => request.post('/user/login', data)
export const logout = () => request.post('/user/logout')
