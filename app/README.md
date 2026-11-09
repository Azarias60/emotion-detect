# 健康与情绪检测前端

Vue 3 + Vite + Pinia。检测页面、功能流程、采集能力和 API 按职责拆分，完整依赖关系见 [架构说明](../docs/architecture.md)。

```sh
npm install
npm run dev
npm test
npm run build
```

Node 版本要求见 `package.json`。开发环境通过 `/python-api` 代理到 `127.0.0.1:8081`；部署时可设置 `VITE_API_BASE_URL`，管理、检测和硬件预览共用该地址。

- `src/views/detect`：页面模板和交互入口。
- `src/features/detection`：情绪、健康、疲劳流程，结果映射和硬件设置。
- `src/composables`：共享请求生命周期、录制、问答、倒计时与导航守卫。
- `src/api`：领域 API、统一 HTTP 工厂和请求序列化。
- `src/utils`：纯数据格式化等工具。
- `src/styles/detection`：检测页面 scoped 样式。
- `tests`：使用 Node 内置测试运行器的契约、资源生命周期和功能流程测试。

情绪、健康、疲劳检测全部使用后端单片机采集；前端仅展示硬件视频流、控制采集并提交问诊上下文，不访问浏览器摄像头或麦克风。运行实际检测需要相应设备、模型和后端服务。
