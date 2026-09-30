@echo off
title Chrome CDP for ChatGPT Relay
echo ===================================================
echo Membuka Google Chrome dengan Remote Debugging (9222)
echo ===================================================

set CHROME_EXE=""
if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" set CHROME_EXE="C:\Program Files\Google\Chrome\Application\chrome.exe"
if exist "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" set CHROME_EXE="C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" set CHROME_EXE="%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"

if %CHROME_EXE%=="" (
    echo [ERROR] Google Chrome tidak ditemukan di path standar!
    echo Silakan install Chrome atau ubah path di file start_chrome.bat
    pause
    exit /b 1
)

start "" %CHROME_EXE% ^
  --remote-debugging-port=9222 ^
  --user-data-dir="%LOCALAPPDATA%\Google\Chrome\RelayProfile" ^
  --remote-allow-origins=* ^
  --no-first-run ^
  --no-default-browser-check ^
  https://chatgpt.com/

echo.
echo [OK] Chrome berhasil dijalankan!
echo Silakan LOGIN ke akun ChatGPT Anda di jendela browser yang terbuka.
echo Biarkan tab chatgpt.com tetap terbuka.
timeout /t 5
