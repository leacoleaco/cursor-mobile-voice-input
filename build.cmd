@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist icon.ico (
    echo [build] Error: icon.ico not found, please create icon.ico first
    pause
    exit /b 1
)

echo [build] Checking PyInstaller...
pip install pyinstaller -q 2>nul

echo [build] Building exe...
pyinstaller --onefile --windowed --noconfirm --clean ^
  --name CursorMobileVoiceInput ^
  --add-data "index.html;." ^
  --add-data "icon.ico;." ^
  --add-data "locales;locales" ^
  --hidden-import paramiko ^
  --icon icon.ico ^
  server.py

if %ERRORLEVEL% neq 0 (
    echo [build] Build failed
    pause
    exit /b 1
)

echo.
echo [build] Done: dist\CursorMobileVoiceInput.exe
pause
