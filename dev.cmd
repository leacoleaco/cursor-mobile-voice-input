@echo off
chcp 65001 >nul
cd /d "%~dp0"

set LANVOICE_DEV=1
echo [dev] 开发模式（关闭二维码窗口即退出）
echo.
python server.py
