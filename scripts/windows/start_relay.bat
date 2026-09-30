@echo off
title ChatGPT Relay Runner (:20250)
cd /d "%~dp0"

echo ===================================================
echo Menjalankan ChatGPT Relay Server + CDP Driver
echo ===================================================

if not exist "venv\Scripts\python.exe" (
    echo [ERROR] Virtualenv belum terinstall!
    echo Silakan jalankan install.bat terlebih dahulu.
    pause
    exit /b 1
)

echo [1/2] Menjalankan Server API di port 20250...
start "ChatGPT Relay Server (:20250)" venv\Scripts\python.exe chatgpt_relay_server.py

timeout /t 2 /nobreak >nul

echo [2/2] Menjalankan CDP Driver...
start "ChatGPT CDP Driver" venv\Scripts\python.exe cdp_relay_driver.py

echo.
echo ===================================================
echo [BERHASIL] Relay aktif di http://127.0.0.1:20250
echo Cek status: buka browser ke http://127.0.0.1:20250/status
echo Endpoint API: http://127.0.0.1:20250/v1/chat/completions
echo ===================================================
timeout /t 5
