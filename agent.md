# Agent 运行规范

## 后台任务与服务启动约定

- **禁止将长期运行的后台服务或任务与 Antigravity harness 绑定**：
  - 启动前端（`5174`）、`server`（`8000`）、`Emotion-Detect-py`（`8081`）、数字人引擎（`8020`）、ComfyUI（`8188`）或 `make_avatar.py` 等长耗时任务时，**不要**使用 `IsDaemon: true` 或直接前台阻塞挂靠在当前会话进程树下。
  - 必须使用脱离当前进程树的独立启动方式（例如 Windows 下通过 PowerShell `Start-Process -WindowStyle Hidden` 或项目内的 `start_all.ps1`），并可按需将 stdout/stderr 重定向到日志文件，确保即使关闭或重启 Antigravity，所有后台服务与产线任务仍继续独立运行。
