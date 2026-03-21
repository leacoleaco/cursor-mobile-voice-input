@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo [build-remote-client] Installing dependencies...
pip install -r requirements-remote-client.txt -q
pip install pyinstaller -q

echo [build-remote-client] Building RemoteVoiceClient.exe ...
pyinstaller --onefile --windowed --noconfirm --clean ^
  --name RemoteVoiceClient ^
  --collect-submodules faster_whisper ^
  --collect-all ctranslate2 ^
  --hidden-import sounddevice ^
  --hidden-import numpy ^
  --hidden-import pynput ^
  remote_voice_client.py

if %ERRORLEVEL% neq 0 (
    echo [build-remote-client] Build failed
    pause
    exit /b 1
)

copy /Y "remote_client_config.example.json" "dist\remote_client_config.example.json" >nul 2>&1

echo.
echo [build-remote-client] Done: dist\RemoteVoiceClient.exe
echo [build-remote-client] Copy remote_client_config.example.json to remote_client_config.json and edit ws_url/token.
pause
