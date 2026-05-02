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
Write-Host "  Backend  -> http://localhost:8001/docs"
Write-Host "  Frontend -> http://localhost:5173"
Write-Host ""
Write-Host "Press Ctrl+C to stop"
Write-Host "=============================="

$backend = Start-Process -FilePath "python" -ArgumentList "-m","uvicorn","app.backend.main:app","--host","0.0.0.0","--port","8001","--reload" -WorkingDirectory $PSScriptRoot -PassThru -NoNewWindow
$frontend = Start-Process -FilePath "npm.cmd" -ArgumentList "run","dev" -WorkingDirectory "$PSScriptRoot\app/frontend" -PassThru -NoNewWindow

try {
    while (-not $backend.HasExited -and -not $frontend.HasExited) {
        Start-Sleep -Seconds 1
    }
} finally {
    if (-not $backend.HasExited) { Stop-Process -Id $backend.Id -ErrorAction SilentlyContinue }
    if (-not $frontend.HasExited) { Stop-Process -Id $frontend.Id -ErrorAction SilentlyContinue }
}
