import { defineConfig } from 'playwright/test'

// 使用效果验收单独运行，不作为生命周期与架构测试的通过依据。
export default defineConfig({
  testDir: 'acceptance',
  workers: 1,
  forbidOnly: !!process.env.CI,
  timeout: 30_000,
  expect: { timeout: 5_000 },
  use: { baseURL: 'http://127.0.0.1:4173' },
  webServer: {
    command: 'npm run preview -- --host 127.0.0.1 --port 4173 --strictPort',
    port: 4173,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
  projects: [
    { name: 'desktop', testMatch: /browser-journey/, use: { viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1 } },
    ...['chromium', 'firefox', 'webkit'].map(browserName => ({
      name: `glass-${browserName}-desktop`, testMatch: /scene\.spec/,
      use: { browserName, viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1 },
    })),

  ],
})
