import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'
import { configureBackendProxy } from './config/backendProxy.js'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue(), vueDevTools()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        manualChunks: {
          'vue-vendor': ['vue', 'vue-router', 'pinia'],
                    axios: ['axios'],
        },
      },
    },
  },
  server: {
    proxy: {
      '/python-api': {
        target: 'http://127.0.0.1:8081',
        configure: configureBackendProxy,
        changeOrigin: true,
        // 单实例闸门走 /python-api/app-instance/ws，必须让代理转发 Upgrade
        ws: true,
        rewrite: (path) => path.replace(/^\/python-api/, ''),
      },
    },
  },
})
