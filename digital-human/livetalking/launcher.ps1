# 数字人引擎快捷启动器（引擎已在 detect 项目内，后端 8081 启动时也会自动拉起）（配套桌面快捷方式"数字人"）
#
# 流程：结束旧服务 → 以"网页关闭即自动退出"模式启动 → 等模型就绪 → 打开浏览器。
# 页面全部关闭后，服务器在数秒内自动退出（server/lifecycle.py 看门狗）。
$ErrorActionPreference = "Continue"

$py  = "E:\emotionDetect\digital-human\livetalking\venv\Scripts\python.exe"
$wd  = "E:\emotionDetect\digital-human\livetalking"
$url = "http://localhost:8020/"

Write-Host "正在启动数字人，请稍候（模型加载约半分钟）..." -ForegroundColor Cyan

# 1) 结束已有的 app.py 进程（venv 的 python.exe 可能只是父进程，必须按进程树结束）
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*app.py*" }
foreach ($p in $procs) {
    taskkill /F /T /PID $p.ProcessId 2>$null | Out-Null
}
if ($procs) { Start-Sleep -Seconds 2 }

# 2) 启动服务：LIVETALKING_AUTOEXIT=1 → 所有数字人网页关闭后自动退出；
#    浏览器由本脚本在就绪后统一打开，不让 app.py 自己抢先弹
$env:LIVETALKING_AUTOEXIT   = "1"
$env:LIVETALKING_NO_BROWSER = "1"
Start-Process -FilePath $py -WorkingDirectory $wd -WindowStyle Hidden `
    -RedirectStandardOutput "$env:TEMP\livetalking-launcher-out.log" `
    -RedirectStandardError  "$env:TEMP\livetalking-launcher-err.log" `
    -ArgumentList @(
        "app.py", "--transport", "webrtc", "--model", "wav2lip",
        "--avatar_id", "my_avatar", "--tts", "cosyvoice3",
        "--listenport", "8020", "--batch_size", "4",
        "--webrtc_video_queue_size", "6", "--webrtc_audio_queue_size", "12"
    )

# 3) 等模型就绪：/api/assets 只等端口，/api/readiness 才等预热完成
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 2
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:8020/api/readiness" -UseBasicParsing -TimeoutSec 2
        if ($r.StatusCode -eq 200 -and ($r.Content | ConvertFrom-Json).ready) { $ready = $true; break }
    } catch { }
}

# 4) 打开浏览器（未就绪也打开：页面自己会显示加载进度）
Start-Process $url
Write-Host "已打开浏览器。关闭所有数字人网页后，服务会自动退出。" -ForegroundColor Green
if (-not $ready) {
    Add-Type -AssemblyName System.Windows.Forms | Out-Null
    [System.Windows.Forms.MessageBox]::Show(
        "服务启动较慢，浏览器已打开，页面会显示加载进度，就绪后即可对话。",
        "数字人") | Out-Null
}
