@echo off
chcp 65001 >nul
echo 正在停止后端服务...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_backend.ps1" %*
pause
