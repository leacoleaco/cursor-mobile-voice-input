@echo off
chcp 65001 >nul
cd /d "%~dp0."

if /i "%CONDA_DEFAULT_ENV%"=="remote-input" goto conda_ok
echo [dev] Activating conda environment: remote-input
call conda activate remote-input
if errorlevel 1 (
    echo [dev] Error: could not activate conda env remote-input. Use Anaconda Prompt or run conda init cmd.exe.
    pause
    exit /b 1
)
:conda_ok

set LANVOICE_DEV=1
echo [dev] 开发模式（关闭二维码窗口即退出）
echo.
python server.py
