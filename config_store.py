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
        global CONFIG_DATA, COMMANDS, USER_IP, LLM_ENABLED, LLM_MODEL, LLM_BASE_URL
        CONFIG_DATA = data
        COMMANDS = _normalize_commands(data.get("commands"))
        ip = (data.get("user_ip") or "").strip()
        USER_IP = ip if ip else None
        LLM_ENABLED = bool(data.get("llm_enabled", False))
        LLM_MODEL = (data.get("llm_model") or "qwen3.5:0.8b").strip() or "qwen3.5:0.8b"
        LLM_BASE_URL = (data.get("llm_base_url") or "http://127.0.0.1:11434").strip() or "http://127.0.0.1:11434"

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

    USER_IP = None
    CONFIG_DATA = {"user_ip": None, "commands": [], "llm_enabled": False, "llm_model": "qwen3.5:0.8b", "llm_base_url": "http://127.0.0.1:11434"}
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
    global CONFIG_PATH_IN_USE, CONFIG_DATA, COMMANDS, LLM_ENABLED, LLM_MODEL, LLM_BASE_URL
    data = dict(CONFIG_DATA) if isinstance(CONFIG_DATA, dict) else {}
    data["user_ip"] = USER_IP
    data["commands"] = COMMANDS
    data["llm_enabled"] = LLM_ENABLED
    data["llm_model"] = LLM_MODEL
    data["llm_base_url"] = LLM_BASE_URL

    if _try_write_json(CONFIG_PATH_PRIMARY, data):
        CONFIG_PATH_IN_USE = CONFIG_PATH_PRIMARY
        return

    if _try_write_json(CONFIG_PATH_FALLBACK, data):
        CONFIG_PATH_IN_USE = CONFIG_PATH_FALLBACK
        return
