# PhysioSense V2 前端

React / TypeScript / Vite / Tailwind v4。玻璃由 [simple-liquid-glass](https://github.com/lucaperullo/simple-liquid-glass) 实时渲染：`<LiquidGlass>` 与 `LiquidGlassScene` 是唯一的玻璃通路，DOM 负责布局与交互。没有按视口烘焙的玻璃图、没有额外分辨率资产、也没有 CSS 自写的替代折射。

## 当前实现

本节记录重写前的代码基线。已确认的待实施方案统一见[界面重写设计与业务契约](docs/design/README.md)；方案中的组件与行为尚未接入。

- 首页右上角提供唯一账号入口；未登录不挂载检测、报告、综合分析与数字人页面。
- 工作页共用一个主玻璃面，顶部账号头像使用独立圆形玻璃；检测三模式共享准备、60秒采集、自动分析、展示/保存流程。健康先拍舌象，疲劳无问答，健康/情绪自动问答。
- 测量项与 main 核对，缺测显示“—”；FacePhys 六项仅在展开生理窗口后显示。冻结的单片机心率/温度保留在 capture 内，不进入 HTTP 或 UI。
- 报告筛选、分页、删除、详情、综合统计与近期报告均使用真实 API；参数、操作成功和页面可见性驱动读取。
- 设备视频仅订阅后端同源 WebRTC，不调用浏览器摄像头。设备离线仅写“设备未连接”，数字人仅写“未接入”。

## 环境与执行

需要 Node.js 22+；前端依赖只有 npm 包（含 `simple-liquid-glass`），没有 JVM/Gradle/Kotlin 步骤。渲染策略按浏览器分裂：桌面 Chromium 用 SVG `backdrop-filter` 实时采样面板后面的 DOM，iOS/Safari/Firefox 走 WebGL（需要 scene provider），没有可捕获源时退到 CSS 兜底。后端开发代理 `/python-api` → `http://127.0.0.1:8000/api/v1`；部署通过 `VITE_API_BASE_URL` 配置真实接口。

```bash
cd frontend
npm ci
npm run dev                 # 正常 Vite HMR（无预构建步骤）
npm test                    # 领域、流程、依赖边界与六页面契约检查
npm run build               # tsc -b + Vite 生产构建
npm run preview             # 生产预览
npx playwright install chromium firefox webkit
npm run verify -- --output output/journey   # 桌面浏览器交互验收，运行器管理预览服务
npm run verify:glass -- --output output/glass # 桌面三引擎玻璃验收
```

玻璃由 `src/components/GlassPanel.tsx` 统一接入，工作区和圆形头像共用一个 LiquidGlassScene。应用外壳装配工作区，CSS 负责版式，`UiProvider` 统一控件主题。详见 [玻璃边界](docs/glass/README.md)。

部署需要 SPA 回退到 `index.html`。建议服务器启用 Brotli/gzip，并对带哈希的 `assets/*` 使用长期缓存；缩放靠实时渲染，而不是预生成多档图片。

[所有权与业务边界](docs/architecture/six-page-shell-refactor.md)、[玻璃契约](docs/glass/README.md)。库的 MIT 许可证与第三方 notices 随站点分发至 `/licenses/simple-liquid-glass-MIT.txt` 与 `/licenses/simple-liquid-glass-THIRD-PARTY-NOTICES.txt`。
