@echo off
title Installer ChatGPT Relay Windows
cd /d "%~dp0"

echo ========================================================
echo   INSTALLER CHATGPT WEB RELAY & TOOL CALLING (WINDOWS)
echo ========================================================
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python tidak terdeteksi di sistem Windows Anda!
    echo Silakan download dan install Python dari: https://www.python.org/downloads/
    echo PENTING: Centang opsi "Add python.exe to PATH" saat menginstall.
    echo.
    pause
    exit /b 1
)

echo [1/3] Membuat Python Virtual Environment (venv)...
if not exist "venv" (
    python -m venv venv
)

echo [2/3] Menginstall library Python yang dibutuhkan...
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\pip.exe install -q fastapi uvicorn websockets httpx

echo [3/3] Instalasi dependensi selesai!
echo.
echo ========================================================
echo                PANDUAN MENJALANKAN
echo ========================================================
echo 1. Jalankan "start_chrome.bat"
echo    -> Jendela Chrome akan terbuka, login ke chatgpt.com
echo.
echo 2. Jalankan "start_relay.bat"
echo    -> Relay server (:20250) & CDP driver akan aktif
echo.
echo 3. Di VSCode / Cursor / Cline / Continue:
echo    - Base URL : http://localhost:20250/v1
echo    - API Key  : dummy (bebas)
echo    - Model    : chatgpt-web
echo ========================================================
echo.
pause
