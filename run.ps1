# CodePilot 一键启动（分开窗口，避免 reload 冲突）
# 用法：在 PowerShell 中运行 .\run.ps1

$host.UI.RawUI.WindowTitle = "CodePilot"
Set-Location $PSScriptRoot

Write-Host "=============================="
Write-Host " CodePilot"
Write-Host "=============================="
Write-Host ""

Write-Host "[1/3] Checking Python deps..." -NoNewline
pip install -r requirements.txt -q 2>$null
Write-Host " OK"

Write-Host "[2/3] Checking Frontend deps..." -NoNewline
Push-Location app/frontend
if (-not (Test-Path node_modules)) {
    npm install -q 2>$null
}
Pop-Location
Write-Host " OK"

Write-Host "[3/3] Starting servers..."
Write-Host ""
Write-Host "  Backend  -> http://localhost:8000/docs"
Write-Host "  Frontend -> http://localhost:5173"
Write-Host ""
Write-Host "Close each window to stop"
Write-Host "=============================="

# 启动后端（新窗口）
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PSScriptRoot'; python -m uvicorn app.backend.main:app --host 0.0.0.0 --port 8000 --reload"

# 启动前端（新窗口）
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PSScriptRoot\app\frontend'; npm run dev"
