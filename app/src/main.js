import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import { createPinia } from 'pinia'
import { startInstanceGuard } from './composables/useSingleInstance'

// 先占住"唯一前端实例"的槽位，再挂载页面：新窗口一打开就顶掉旧窗口，
// 避免旧窗口还能继续占着麦克风/数字人会话（后端见 routers/instance.py）。
startInstanceGuard()

const app = createApp(App)
const pinia = createPinia()
app.use(router)
app.use(pinia)
app.mount('#app')
