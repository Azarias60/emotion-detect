@echo off
rem ============================================================
rem  ComfyUI 安全显存启动（防驱动 TDR 风暴）
rem  与 nvidia_gpu一键启动.bat 的区别：
rem    --vram-headroom 1.5      DynamicVRAM 始终保持 1.5G 显存空闲（给驱动/显示/其他程序）
rem    --fast fp16_accumulation  官方加速模式，大模型长内核更稳更快
rem  看门狗逻辑与主启动器一致（关网页约90秒后自动关服务）
rem  创建于 2026-11-06：当日 24h 内 84 次 nvlddmkm TDR，根因是工作流显存超载
rem ============================================================
start "comfy-watchdog" /min powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0comfy-watchdog.ps1" -StopSentinel "%~dp0comfy-watchdog.stop"
.\python_embeded\python.exe -s ComfyUI\main.py --windows-standalone-build --enable-manager --vram-headroom 1.5 --fast fp16_accumulation
if exist "%~dp0comfy-watchdog.stop" ( del "%~dp0comfy-watchdog.stop" ) else ( echo If you see this and ComfyUI did not start try updating your Nvidia Drivers to the latest. If you get a c10.dll error you need to install vc redist that you can find: https://aka.ms/vc14/vc_redist.x64.exe & pause )
