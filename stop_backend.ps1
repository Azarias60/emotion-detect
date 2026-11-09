# 一键彻底关闭多模态情绪检测后端服务
# 使用方法：
#   .\stop_backend.ps1        # 仅关闭 FastAPI 后端 (8081 端口)
#   .\stop_backend.ps1 -All   # 同时关闭 FastAPI 后端与本地大模型服务 (1919 端口)

param (
    [switch]$All
)

function Stop-PortProcess {
    param (
        [int]$Port,
        [string]$ServiceName
    )
    
    $connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
    if ($connections) {
        $pids = $connections | Select-Object -ExpandProperty OwningProcess -Unique
        foreach ($procId in $pids) {
            if ($procId -gt 0) {
                try {
                    $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
                    $procName = if ($p) { $p.ProcessName } else { "PID $procId" }
                    Write-Host "🛑 正在终止 $ServiceName [进程: $procName, PID: $procId]..." -ForegroundColor Yellow
                    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
                } catch {
                    Write-Host "⚠️ 终止进程 $procId 失败: $_" -ForegroundColor DarkYellow
                }
            }
        }
        Start-Sleep -Milliseconds 300
        # 再次确认
        $check = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
        if (-not $check) {
            Write-Host "✅ $ServiceName 端口 $Port 已彻底释放！" -ForegroundColor Green
        } else {
            Write-Host "⚠️ $ServiceName 端口 $Port 仍有连接，尝试使用 taskkill 强制清理..." -ForegroundColor Yellow
            $pids | ForEach-Object { taskkill /F /T /PID $_ 2>$null }
        }
    } else {
        Write-Host "ℹ️ $ServiceName (端口 $Port) 未在运行，无需停止。" -ForegroundColor Cyan
    }
}

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "   多模态健康与情绪检测服务 - 停止工具   " -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan

# 1. 停止 FastAPI 后端服务 (8081)
Stop-PortProcess -Port 8081 -ServiceName "FastAPI 后端"

# 2. 如果指定了 -All，停止本地大模型服务 (1919)
if ($All) {
    Stop-PortProcess -Port 1919 -ServiceName "本地大模型 (llama-server)"
} else {
    $llmConn = Get-NetTCPConnection -LocalPort 1919 -ErrorAction SilentlyContinue
    if ($llmConn) {
        Write-Host "💡 提示: 本地大模型 (1919 端口) 仍在运行中。若需一并关闭，请执行: .\stop_backend.ps1 -All" -ForegroundColor Gray
    }
}

Write-Host "`n🎯 清理完成！" -ForegroundColor Green
