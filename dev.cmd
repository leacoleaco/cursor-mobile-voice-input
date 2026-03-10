@echo off
chcp 65001 >nul
cd /d "%~dp0"

set LANVOICE_DEV=1
echo [dev] 开发模式（热重载：修改 .py 后自动重启）
echo.
python server.py
