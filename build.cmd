@echo off
chcp 65001 >nul
cd /d "%~dp0."

if /i "%CONDA_DEFAULT_ENV%"=="remote-input" goto conda_ok
echo [build] Activating conda environment: remote-input
call conda activate remote-input
if errorlevel 1 (
    echo [build] Error: could not activate conda env remote-input. Use Anaconda Prompt or run conda init cmd.exe.
    pause
    exit /b 1
)
:conda_ok

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
