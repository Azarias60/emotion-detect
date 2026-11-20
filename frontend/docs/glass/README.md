# 玻璃边界

`src/components/GlassPanel.tsx` 是 simple-liquid-glass 的唯一接入点。`GlassScene` 挂载在应用会话之上，为主工作区和圆形账号头像提供同一个背景。背景使用静态深蓝灰光晕。玻璃的后台捕获、渲染和回退由库负责。

`WorkSurface.tsx` 提供工作区容器，由 `AppShell` 和 `AuthShell` 装配。每个可见页面有一个主玻璃面，有导航栏的页面另有一个圆形玻璃头像。导航、正文分栏和弹窗分别由应用外壳、页面布局和 Ant Design 管理。正文在主阅读区滚动，头像保持可点击和可聚焦。

`UiProvider` 为 Ant Design 与 Ant Design X 提供主题。页面样式消费同一套文字、表面和分隔线变量，CSS Grid 与 Flexbox 负责布局。

## 验证

- `npm test` 检查玻璃库和主题的唯一归属以及业务契约。
- `npm run build` 生成生产预览。
- `npm run verify` 验证登录、导航、查询、报告与数字人入口。
- `npm run verify:glass` 在 Chromium、Firefox 和 WebKit 检查可见玻璃数量、渲染策略、账号入口和桌面布局。
