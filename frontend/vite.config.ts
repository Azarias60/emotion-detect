import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5174,
    proxy: {
      // 数字人引擎（8020）：WebRTC offer、WS 对话事件流、模型 Key、素材管理
      '/dh-api': { target: 'http://127.0.0.1:8020', changeOrigin: true,
        rewrite: path => path.replace(/^\/dh-api/, '') },
      '/dh-ws': { target: 'ws://127.0.0.1:8020', ws: true,
        rewrite: path => path.replace(/^\/dh-ws/, '') },
      // 制作/克隆后端（Emotion-Detect-py 8081）：照片产线 + 语音克隆
      '/emotion-api': { target: 'http://127.0.0.1:8081', changeOrigin: true,
        rewrite: path => path.replace(/^\/emotion-api/, '') },
      '/python-api/uploads': { target: 'http://127.0.0.1:8000', changeOrigin: true,
        rewrite: path => path.replace(/^\/python-api/, '') },
      '/python-api': { target: 'http://127.0.0.1:8000', changeOrigin: true,
        rewrite: path => path.replace(/^\/python-api/, '/api/v1') },
    },
  },
})
