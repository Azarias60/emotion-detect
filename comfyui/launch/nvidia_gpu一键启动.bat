@echo off
rem ---- webpage-close watchdog: auto stops ComfyUI ~90s after the web page stays closed (delete the next line to disable) ----
start "comfy-watchdog" /min powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0comfy-watchdog.ps1" -StopSentinel "%~dp0comfy-watchdog.stop"
.\python_embeded\python.exe -s ComfyUI\main.py --windows-standalone-build --enable-manager
if exist "%~dp0comfy-watchdog.stop" ( del "%~dp0comfy-watchdog.stop" ) else ( echo If you see this and ComfyUI did not start try updating your Nvidia Drivers to the latest. If you get a c10.dll error you need to install vc redist that you can find: https://aka.ms/vc14/vc_redist.x64.exe & pause )
