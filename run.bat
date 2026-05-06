@echo off
chcp 65001 >nul
title CodePilot Launcher

echo ==============================
echo  CodePilot
echo ==============================
echo.

echo [1/3] Checking Python deps...
pip install -r requirements.txt -q 2>nul
echo  OK

echo [2/3] Checking Frontend deps...
if not exist "app\frontend\node_modules" (
    pushd app\frontend
    call npm install -q 2>nul
    popd
)
echo  OK

echo [3/3] Starting servers...
echo.
echo   Backend  -^> http://localhost:8000/docs
echo   Frontend -^> http://localhost:5173
echo.
echo Close each window to stop
echo ==============================

:: Start backend in new PowerShell window
start "CodePilot Backend" powershell -NoExit -Command "cd '%~dp0'; python -m uvicorn app.backend.main:app --host 0.0.0.0 --port 8000 --reload"

:: Start frontend in new PowerShell window  
start "CodePilot Frontend" powershell -NoExit -Command "cd '%~dp0\app\frontend'; npm run dev"
