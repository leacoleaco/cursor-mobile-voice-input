@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist icon.ico (
    echo [build] 错误: icon.ico 不存在，请先创建 icon.ico
    pause
    exit /b 1
)

echo [build] 检查 PyInstaller...
pip install pyinstaller -q 2>nul

echo [build] 打包 exe...
pyinstaller --onefile --windowed --noconfirm --clean ^
  --name LANVoiceInput ^
  --add-data "index.html;." ^
  --add-data "icon.ico;." ^
  --icon icon.ico ^
  server.py

if %ERRORLEVEL% neq 0 (
    echo [build] 打包失败
    pause
    exit /b 1
)

echo.
echo [build] 完成: dist\LANVoiceInput.exe
pause
