@echo off
REM ============================================================
REM  LiveTalking - 启动数字人流媒体服务 (Windows)
REM  使用前:
REM    1. 确保 models\wav2lip.pth 模型文件存在
REM    2. 确保 Fun-CosyVoice3 模型和参考音频存在
REM    3. 确保 data\avatars\%AVATAR_ID%\ 下有数字人素材
REM ============================================================

setlocal enabledelayedexpansion

echo ============================================================
echo   LiveTalking - Digital Human Streaming Server
echo ============================================================

REM 激活虚拟环境
call venv\Scripts\activate.bat

REM 检查模型
if not exist "models\wav2lip.pth" (
    echo [ERROR] models\wav2lip.pth 未找到！
    echo 请从 https://pan.quark.cn/s/83a750323ef0 下载模型
    pause
    exit /b 1
)

REM 默认配置
set MODEL=wav2lip
set AVATAR_ID=my_avatar
set TRANSPORT=webrtc
set TTS=cosyvoice3
set LISTEN_PORT=8010
set BATCH_SIZE=8

REM 解析命令行参数
:parse_args
if "%~1"=="" goto :run
if "%~1"=="--model"    (set MODEL=%~2       & shift & shift & goto :parse_args)
if "%~1"=="--avatar"   (set AVATAR_ID=%~2   & shift & shift & goto :parse_args)
if "%~1"=="--transport"(set TRANSPORT=%~2   & shift & shift & goto :parse_args)
if "%~1"=="--port"     (set LISTEN_PORT=%~2 & shift & shift & goto :parse_args)
if "%~1"=="--batch"    (set BATCH_SIZE=%~2  & shift & shift & goto :parse_args)
echo 未知参数: %~1
shift
goto :parse_args

:run
echo.
echo 配置:
echo   模型:     %MODEL%
echo   数字人:   %AVATAR_ID%
echo   传输:     %TRANSPORT%
echo   端口:     %LISTEN_PORT%
echo   批大小:   %BATCH_SIZE%
echo   TTS:      %TTS% (本地流式 Fun-CosyVoice3)
echo.
echo 访问地址:
echo   统一入口:      http://localhost:%LISTEN_PORT%/
echo   默认先显示轻量版，点击按钮后切换实时口型模式
echo   WebRTC 旧页面: http://localhost:%LISTEN_PORT%/dashboard.html
echo   RTMP 模式:     http://localhost:%LISTEN_PORT%/rtmpapi.html
echo.

python app.py ^
    --model %MODEL% ^
    --avatar_id %AVATAR_ID% ^
    --transport %TRANSPORT% ^
    --tts %TTS% ^
    --listenport %LISTEN_PORT% ^
    --batch_size %BATCH_SIZE%

pause
