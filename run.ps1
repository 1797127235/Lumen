$host.UI.RawUI.WindowTitle = "CodePilot"
Set-Location $PSScriptRoot

Write-Host "=============================="
Write-Host " CodePilot"
Write-Host "=============================="
Write-Host ""

Write-Host "[1/2] Checking dependencies..." -NoNewline
pip install -r requirements.txt -q 2>$null
Write-Host " OK"

Write-Host "[2/2] Starting server..."
Write-Host ""
Write-Host "  Swagger -> http://localhost:8000/docs"
Write-Host "  Health  -> http://localhost:8000/api/health"
Write-Host ""
Write-Host "Press Ctrl+C to stop"
Write-Host "=============================="

python -m uvicorn app.backend.main:app --host 0.0.0.0 --port 8001 --reload
pause
