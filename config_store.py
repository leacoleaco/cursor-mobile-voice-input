"""
Configuration persistence.
- Prefers writing config.json beside the executable.
- Falls back to user profile when permission is denied.
"""
import json
import os
from typing import List, Optional

from paths import get_exe_dir

# Paths
CONFIG_PATH_PRIMARY = os.path.join(get_exe_dir(), "config.json")
CONFIG_PATH_FALLBACK = os.path.join(os.path.expanduser("~"), "LanVI_config.json")
CONFIG_PATH_IN_USE = CONFIG_PATH_PRIMARY

# Runtime mirrors of config content.
USER_IP: Optional[str] = None
CONFIG_DATA: dict = {}
COMMANDS: List[dict] = []
LLM_ENABLED: bool = False
LLM_MODEL: str = "qwen3.5:0.8b"
LLM_BASE_URL: str = "http://127.0.0.1:11434"
ACCESS_TOKEN: Optional[str] = None  # Required for HTTP/WS when AUTH_REQUIRED; None = no auth
AUTH_REQUIRED: bool = True  # If True, HTTP/WS require token; if False, no auth (legacy)
LOCALE: str = "zh_CN"  # UI language: zh_CN, en
RUN_IN_BACKGROUND: bool = True  # If True, closing window minimizes to tray; if False, exits app

# TLS/SSL: self-signed certificate for HTTPS/WSS
SSL_ENABLED: bool = False  # When True, server uses HTTPS/WSS with a self-signed cert

# SSH tunnel for public exposure (optional)
SSH_TUNNEL_HOST: Optional[str] = None
SSH_TUNNEL_PORT: int = 22
SSH_TUNNEL_USER: Optional[str] = None
SSH_TUNNEL_PASSWORD: Optional[str] = None  # Prefer key auth; password stored in config
SSH_TUNNEL_KEY_PATH: Optional[str] = None
SSH_REMOTE_PORT: int = 8080  # Port on server to expose (must match local or use GatewayPorts)


def _try_write_json(path: str, data: dict) -> bool:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def _try_read_json(path: str) -> Optional[dict]:
    try:
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _normalize_commands(raw) -> List[dict]:
    if not isinstance(raw, list):
        return []
    return [c for c in raw if isinstance(c, dict)]


def load_config():
    """
    Read config at startup.
    Priority:
    1) exe directory config.json
    2) user profile fallback
    3) create defaults when both missing
    """
    global USER_IP, CONFIG_PATH_IN_USE, CONFIG_DATA, COMMANDS, LLM_ENABLED, LLM_MODEL, LLM_BASE_URL

    def _apply(data: dict):
        global CONFIG_DATA, COMMANDS, USER_IP, LLM_ENABLED, LLM_MODEL, LLM_BASE_URL, ACCESS_TOKEN, AUTH_REQUIRED, LOCALE, RUN_IN_BACKGROUND
        global SSH_TUNNEL_HOST, SSH_TUNNEL_PORT, SSH_TUNNEL_USER, SSH_TUNNEL_PASSWORD, SSH_TUNNEL_KEY_PATH, SSH_REMOTE_PORT
        global SSL_ENABLED
        CONFIG_DATA = data
        COMMANDS = _normalize_commands(data.get("commands"))
        ip = (data.get("user_ip") or "").strip()
        USER_IP = ip if ip else None
        LLM_ENABLED = bool(data.get("llm_enabled", False))
        LLM_MODEL = (data.get("llm_model") or "qwen3.5:0.8b").strip() or "qwen3.5:0.8b"
        LLM_BASE_URL = (data.get("llm_base_url") or "http://127.0.0.1:11434").strip() or "http://127.0.0.1:11434"
        ACCESS_TOKEN = (data.get("access_token") or "").strip() or None
        AUTH_REQUIRED = data.get("auth_required", True)
        LOCALE = (data.get("locale") or "zh_CN").strip() or "zh_CN"
        RUN_IN_BACKGROUND = data.get("run_in_background", True)
        SSH_TUNNEL_HOST = (data.get("ssh_tunnel_host") or "").strip() or None
        SSH_TUNNEL_PORT = int(data.get("ssh_tunnel_port") or 22)
        SSH_TUNNEL_USER = (data.get("ssh_tunnel_user") or "").strip() or None
        SSH_TUNNEL_PASSWORD = (data.get("ssh_tunnel_password") or "").strip() or None
        SSH_TUNNEL_KEY_PATH = (data.get("ssh_tunnel_key_path") or "").strip() or None
        SSH_REMOTE_PORT = int(data.get("ssh_remote_port") or 8080)
        SSL_ENABLED = bool(data.get("ssl_enabled", False))

    data = _try_read_json(CONFIG_PATH_PRIMARY)
    if isinstance(data, dict):
        _apply(data)
        CONFIG_PATH_IN_USE = CONFIG_PATH_PRIMARY
        return

    data = _try_read_json(CONFIG_PATH_FALLBACK)
    if isinstance(data, dict):
        _apply(data)
        CONFIG_PATH_IN_USE = CONFIG_PATH_FALLBACK
        return

    global LOCALE, RUN_IN_BACKGROUND, SSH_TUNNEL_HOST, SSH_TUNNEL_PORT, SSH_TUNNEL_USER, SSH_TUNNEL_PASSWORD, SSH_TUNNEL_KEY_PATH, SSH_REMOTE_PORT, SSL_ENABLED
    USER_IP = None
    LOCALE = "zh_CN"
    RUN_IN_BACKGROUND = True
    SSH_TUNNEL_HOST = None
    SSH_TUNNEL_PORT = 22
    SSH_TUNNEL_USER = None
    SSH_TUNNEL_PASSWORD = None
    SSH_TUNNEL_KEY_PATH = None
    SSH_REMOTE_PORT = 8080
    SSL_ENABLED = False
    CONFIG_DATA = {"user_ip": None, "commands": [], "run_in_background": True, "llm_enabled": False, "llm_model": "qwen3.5:0.8b", "llm_base_url": "http://127.0.0.1:11434", "access_token": None, "locale": "zh_CN", "ssl_enabled": False}
    COMMANDS = []
    LLM_ENABLED = False
    LLM_MODEL = "qwen3.5:0.8b"
    LLM_BASE_URL = "http://127.0.0.1:11434"
    save_config()


def save_config():
    """
    Persist current USER_IP/COMMANDS to disk.
    Prefer exe directory; fall back to user profile when blocked.
    """
    global CONFIG_PATH_IN_USE, CONFIG_DATA, COMMANDS, LLM_ENABLED, LLM_MODEL, LLM_BASE_URL, ACCESS_TOKEN, RUN_IN_BACKGROUND
    global SSH_TUNNEL_HOST, SSH_TUNNEL_PORT, SSH_TUNNEL_USER, SSH_TUNNEL_PASSWORD, SSH_TUNNEL_KEY_PATH, SSH_REMOTE_PORT
    global SSL_ENABLED
    data = dict(CONFIG_DATA) if isinstance(CONFIG_DATA, dict) else {}
    data["user_ip"] = USER_IP
    data["commands"] = COMMANDS
    data["run_in_background"] = RUN_IN_BACKGROUND
    data["llm_enabled"] = LLM_ENABLED
    data["llm_model"] = LLM_MODEL
    data["llm_base_url"] = LLM_BASE_URL
    data["access_token"] = ACCESS_TOKEN
    data["auth_required"] = AUTH_REQUIRED
    data["locale"] = LOCALE
    data["ssh_tunnel_host"] = SSH_TUNNEL_HOST
    data["ssh_tunnel_port"] = SSH_TUNNEL_PORT
    data["ssh_tunnel_user"] = SSH_TUNNEL_USER
    data["ssh_tunnel_password"] = SSH_TUNNEL_PASSWORD
    data["ssh_tunnel_key_path"] = SSH_TUNNEL_KEY_PATH
    data["ssh_remote_port"] = SSH_REMOTE_PORT
    data["ssl_enabled"] = SSL_ENABLED

    if _try_write_json(CONFIG_PATH_PRIMARY, data):
        CONFIG_PATH_IN_USE = CONFIG_PATH_PRIMARY
        return

    if _try_write_json(CONFIG_PATH_FALLBACK, data):
        CONFIG_PATH_IN_USE = CONFIG_PATH_FALLBACK
        return
