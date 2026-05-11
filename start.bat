@echo off
title Lumen
echo.
echo   Lumen Desktop
echo   -------------
echo.

where cargo >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Rust ^(cargo^) not found
    echo Please install Rust from https://rustup.rs/
    pause
    exit /b 1
)

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python not found
    echo Please install Python from https://www.python.org/
    pause
    exit /b 1
)

where node >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Node.js not found
    echo Please install Node.js from https://nodejs.org/
    pause
    exit /b 1
)

if not exist "node_modules\" (
    echo [1/2] Installing npm dependencies...
    call npm install
    if %errorlevel% neq 0 exit /b %errorlevel%
    echo [1/2] Done
) else (
    echo [1/2] Skipped (already installed)
)

echo [2/2] Starting Tauri...
echo.
cargo tauri dev
pause
