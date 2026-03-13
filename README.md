# LAN Voice Input

> **[中文文档](README.zh.md)**

Use your phone as a voice and remote input device for your Windows PC. Access via **LAN** (same WiFi) or **public internet** (SSH tunnel). Designed for [Cursor IDE](https://cursor.com) users, but works with any application that accepts text input.

---

## Why This Tool?

If you use **Cursor IDE** (or any AI coding tool), you know the pain:

- Your PC has no microphone — or the mic quality is poor.
- Windows speech recognition is laggy and inaccurate compared to your phone's keyboard.
- You want to dictate prompts, instructions, or code comments **hands-free**, without touching the keyboard.

**This tool solves all of that.** Run it on your PC, scan a QR code with your phone, and start speaking. Your phone's voice input (which is fast, accurate, and always with you) types directly into whatever is focused on your PC — including Cursor's AI input box.

> **Typical workflow:** Open Cursor → click "Focus Cursor input" on your phone → speak your prompt → tap Send. Done. No keyboard needed.

---

## Screenshots

### Service (PC side)

![Service window — QR code for phone to scan](readme/service.png)

Run the app on your PC. A window shows a QR code and the access URL. Select your network interface (LAN mode for phones on the same WiFi). Scan with your phone to connect instantly.

### Client (Phone side)

![Client web page — voice input interface on phone](readme/client.png)

Open the URL on your phone. Tap the input box, activate your phone's keyboard, and use **voice input**. The text is sent to your PC in real time. Dedicated buttons let you jump straight to Cursor's prompt input (`Focus Cursor input`) or start a new agent session (`New Agent`).

---

## Features

- **Voice & Text Input** — Use your phone's keyboard (including voice input) to type into your PC. Tap the input box, bring up the mobile keyboard, and speak or type.
- **Local Ollama Intent Analysis** — Call a local [Ollama](https://ollama.ai) model to analyze user voice commands and infer intent. When exact string matching fails, the LLM maps natural-language phrases to executable actions (e.g. "open file transfer" → run LocalSend).
- **Command Mode** — Natural-language voice commands: pause/continue input, newline, punctuation (comma, period, etc.), delete N characters, clear input. Optional LLM-assisted fuzzy matching via Ollama when exact match fails.
- **Mouse Control** — Swipe to move cursor, tap for left click, long-press for right click, two-finger scroll.
- **Virtual Keyboard** — Full on-screen keyboard with Ctrl, Alt, Shift, Win modifiers and common shortcuts.
- **Clipboard Sync** — Server pushes PC clipboard to the phone; tap to copy.
- **Cursor Shortcuts** — Quick buttons for "Jump to Cursor input" (Ctrl+I) and "New Agent" (Ctrl+N).
- **SSH Public Access** — Use an SSH tunnel to access the service over the internet when your phone is not on the same LAN. Configure via "Tunnel config" in the QR window; the app supports Local, LAN, and Public (internet) access modes.

---

## How It Works

1. Run the app on your Windows PC. A QR code window opens.
2. Scan the QR code with your phone (both devices must be on the same WiFi).
3. Open the web page on your phone and start typing or speaking.
4. Text is sent to the focused window on your PC via WebSocket.

The server listens only on `127.0.0.1`; access from your phone is via a LAN URL (e.g. `http://192.168.1.x:port`). HTTP and WebSocket share the same port.

---

## Requirements

- Windows 10/11
- Python 3.8+ (for development)
- Same LAN for PC and phone

---

## Quick Start

### Option 1: Run from source

```bash
# Install dependencies
pip install -r requirements.txt

# Run the server
python server.py
```

### Option 2: Build standalone executable

1. Create `icon.ico` in the project root.
2. Run `build.cmd`.
3. Run `dist\CursorMobileVoiceInput.exe`.

### Development mode

```bash
dev.cmd
```

Runs in dev mode: QR window only (no tray), closes when the QR window is closed.

---

## Configuration

Config is stored in `config.json` (next to the exe or in the project root). Key options:

| Key | Description |
|-----|-------------|
| `user_ip` | Preferred LAN IP for QR code (null = auto-detect) |
| `llm_enabled` | Enable LLM fuzzy command matching (default: false) |
| `llm_model` | Ollama model name (e.g. `qwen3.5:0.8b`) |
| `llm_base_url` | Ollama API URL (e.g. `http://127.0.0.1:11434`) |
| `commands` | Custom voice commands (see below) |

### Custom commands

Add entries to `commands` in `config.json`:

```json
{
  "name": "Open LocalSend",
  "match-string": "打开文件传输",
  "command": "E:\\soft\\LocalSend\\localsend_app.exe",
  "args": []
}
```

- `match-string`: Exact or LLM-matched phrase to trigger the command.
- `command`: Executable path or command.
- `args`: Optional list of arguments.

---

## Project Structure

| Module | Purpose |
|--------|---------|
| `server.py` | Main entry, startup, threading |
| `paths.py` | Executable/resource path resolution |
| `config_store.py` | Config load/save |
| `settings.py` | Constants and behavior flags |
| `ip_utils.py` | Port selection, IP enumeration, URL building |
| `notifier.py` | Tray balloon and Windows Toast |
| `input_control.py` | SendInput injection, focus, clipboard |
| `commands.py` | Voice command parsing and execution |
| `text_handler.py` | Deduplication, text/command dispatch |
| `http_server.py` | Flask + WebSocket server |
| `qr_window.py` | QR code window and IP selection |
| `tray_app.py` | System tray menu |
| `llm_assistant.py` | Optional Ollama-based command matching |

---

## Thanks

**Special thanks to the author of https://github.com/bfilestor/lan-voice-input for open sourcing the project and inspiring me. The codebase made it possible for me to build and extend this remote voice input tool tailored for Cursor — it's doubled my workflow efficiency!**

## License

MIT License — see [LICENSE](LICENSE).
