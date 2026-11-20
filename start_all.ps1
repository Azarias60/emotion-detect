# 一键启动多模态情绪检测系统（2026-11-20 合并后的四服务）
#   5174 新前端（frontend/，React + AntD6）
#   8000 physiosense 服务（server/）
#   8081 情绪检测后端（Emotion-Detect-py/，启动时自动带起 8020 数字人引擎）
# 8020 由 8081 后端 lifespan 自动管理，不在这里手动拉。
# 用法：双击同目录 start_all.bat，或 powershell -ExecutionPolicy Bypass -File start_all.ps1

$ErrorActionPreference = 'SilentlyContinue'
$root   = 'E:\emotionDetect'
$node   = 'E:\ZCodeWorkspace\node\node-v22.23.2-win-x64\node.exe'
$python = 'E:\ZCodeWorkspace\venv\Scripts\python.exe'

function Test-Listening([int]$port) {
    return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

Write-Host '=== 启动多模态情绪检测系统 ===' -ForegroundColor Cyan

if (-not (Test-Listening 8000)) {
    $env:PHYSIOSENSE_ALLOW_MISSING_MODELS = '1'
    Start-Process -FilePath $python `
        -ArgumentList '-m','uvicorn','server.app:create_app','--factory','--host','127.0.0.1','--port','8000' `
        -WorkingDirectory $root -WindowStyle Minimized
    Write-Host '已启动 physiosense 服务 (8000)'
} else { Write-Host '8000 已在运行' }

if (-not (Test-Listening 8081)) {
    Start-Process -FilePath $python `
        -ArgumentList '-m','uvicorn','app_api:app','--host','127.0.0.1','--port','8081' `
        -WorkingDirectory "$root\Emotion-Detect-py" -WindowStyle Minimized
    Write-Host '已启动情绪检测后端 (8081)'
} else { Write-Host '8081 已在运行' }

if (-not (Test-Listening 5174)) {
    Start-Process -FilePath $node `
        -ArgumentList 'frontend\node_modules\vite\bin\vite.js','frontend','--host','127.0.0.1','--port','5174','--strictPort' `
        -WorkingDirectory $root -WindowStyle Minimized
    Write-Host '已启动新前端 (5174)'
} else { Write-Host '5174 已在运行' }

# 等前端就绪后打开默认浏览器
for ($i = 0; $i -lt 60; $i++) {
    if (Test-Listening 5174) { break }
    Start-Sleep -Seconds 1
}
Start-Process 'http://127.0.0.1:5174/'
Write-Host '已打开浏览器。关闭页面 90 秒后看门狗会自动停掉全部服务。' -ForegroundColor Green

# 顺带拉起桌面看门狗（存在才启动）
$watchdog = Join-Path $env:USERPROFILE 'Desktop\emotion-watchdog.ps1'
if (Test-Path $watchdog) {
    Start-Process powershell -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',$watchdog -WindowStyle Hidden
}

