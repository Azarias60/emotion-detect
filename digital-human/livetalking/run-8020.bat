@echo off
REM ============================================================
REM  LiveTalking - restart on port 8020 (matches the running setup)
REM
REM  Why this file exists:
REM    run.bat defaults to LISTEN_PORT=8010 and BATCH_SIZE=8,
REM    but the working setup uses 8020 / batch_size 4.
REM    Running run.bat by mistake silently starts a *different*
REM    service (wrong port, different batching).
REM
REM  Usage: double-click, or run from a terminal.
REM ============================================================

setlocal

REM 引擎自带“启动即弹浏览器”的行为；由 detect 后端/本脚本统一控制窗口，
REM 服务本身永远静默启动（后端 core/dh_engine.py 也设了同一变量）。
set LIVETALKING_NO_BROWSER=1

cd /d "%~dp0"

set MODEL=wav2lip
set AVATAR_ID=my_avatar
set TRANSPORT=webrtc
set TTS=cosyvoice3
set LISTEN_PORT=8020
set BATCH_SIZE=4

if not exist "venv\Scripts\python.exe" (
    echo [ERROR] venv\Scripts\python.exe not found. Run this from the project root.
    pause
    exit /b 1
)

echo ============================================================
echo   LiveTalking - port %LISTEN_PORT%, batch %BATCH_SIZE%
echo   open: http://localhost:%LISTEN_PORT%/
echo ============================================================
echo.

venv\Scripts\python.exe app.py ^
    --transport %TRANSPORT% ^
    --model %MODEL% ^
    --avatar_id %AVATAR_ID% ^
    --tts %TTS% ^
    --listenport %LISTEN_PORT% ^
    --batch_size %BATCH_SIZE% ^
    --webrtc_video_queue_size 6 ^
    --webrtc_audio_queue_size 12

pause
