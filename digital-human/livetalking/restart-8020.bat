@echo off
REM ============================================================
REM  LiveTalking - restart on port 8020
REM
REM  Kills whatever is LISTENING on 8020 (the venv launcher's python
REM  child), waits for the port to free, then starts a fresh service
REM  with the same parameters as run-8020.bat.
REM
REM  Why this exists:
REM    Just double-clicking run-8020.bat again does NOT restart —
REM    the old process still holds the port, so the new one fails to
REM    bind and exits. You have to stop the old one first.
REM
REM  Usage: double-click, or run from a terminal.
REM ============================================================

setlocal enabledelayedexpansion
REM 引擎自带“启动即弹浏览器”行为；服务本身永远静默启动。
set LIVETALKING_NO_BROWSER=1

cd /d "%~dp0"

set LISTEN_PORT=8020

echo ============================================================
echo   LiveTalking - restart on port %LISTEN_PORT%
echo ============================================================
echo.

if not exist "venv\Scripts\python.exe" (
    echo [ERROR] venv\Scripts\python.exe not found. Run this from the project root.
    pause
    exit /b 1
)

REM --- 1. 结束占用端口的进程（连同子进程）---
set "PIDS="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%LISTEN_PORT%" ^| findstr "LISTENING"') do (
    set "PIDS=!PIDS! %%p"
)

if defined PIDS (
    for %%p in (!PIDS!) do (
        echo [1/3] 结束监听 %LISTEN_PORT% 的进程 PID %%p ...
        taskkill /F /T /PID %%p >nul 2>&1
    )
) else (
    echo [1/3] 端口 %LISTEN_PORT% 上没有监听进程，跳过
)

REM --- 2. 等端口释放（最多 20 秒）---
echo [2/3] 等待端口释放 ...
set /a WAIT=0
:waitloop
netstat -ano | findstr ":%LISTEN_PORT%" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
    set /a WAIT+=1
    if !WAIT! GEQ 20 (
        echo       端口仍被占用，可能有别的程序在用 %LISTEN_PORT%
        pause
        exit /b 1
    )
    timeout /t 1 /nobreak >nul
    goto waitloop
)
echo       端口已释放

REM --- 3. 启动 ---
echo [3/3] 启动服务 ...
echo.
echo   访问地址: http://localhost:%LISTEN_PORT%/
echo   停止服务: 按 Ctrl+C，或直接关掉这个窗口
echo   模型加载约 10 秒，之后浏览器会自动打开
echo.

venv\Scripts\python.exe app.py ^
    --transport webrtc ^
    --model wav2lip ^
    --avatar_id my_avatar ^
    --tts cosyvoice3 ^
    --listenport %LISTEN_PORT% ^
    --batch_size 4 ^
    --webrtc_video_queue_size 6 ^
    --webrtc_audio_queue_size 12

echo.
echo 服务已退出。
pause
