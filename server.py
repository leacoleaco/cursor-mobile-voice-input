# server.py
# -*- coding: utf-8 -*-
"""Main entry point for LAN Voice Input (modularized)."""
import asyncio
import os
import sys
import threading

from i18n import _

# Fix Windows console encoding for emoji/Unicode (GBK cannot encode emoji)
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import config_store
from config_store import CONFIG_PATH_FALLBACK, CONFIG_PATH_IN_USE, CONFIG_PATH_PRIMARY
from auth_token import generate_token, get_token, set_token
from http_server import run_server
from ip_utils import build_urls, choose_free_port, get_effective_ip, get_ipv4_candidates
from notifier import notify
from qr_window import QRWindowManager
from settings import DEFAULT_HTTP_PORT, QR_FORCE_LOCALHOST
from tray_app import run_tray


def main():
    # Load config first (exe dir config.json when packaged)
    config_store.load_config()
    from i18n import set_locale
    set_locale(config_store.LOCALE)

    # Generate fresh token on every startup when auth is required
    if config_store.AUTH_REQUIRED:
        config_store.ACCESS_TOKEN = generate_token()
        config_store.save_config()
        set_token(config_store.ACCESS_TOKEN)
    else:
        set_token(None)  # No auth

    port = choose_free_port(DEFAULT_HTTP_PORT)

    qr_ip = "127.0.0.1" if QR_FORCE_LOCALHOST else get_effective_ip(config_store.USER_IP)
    qr_url, qr_payload_url = build_urls(qr_ip, port, port, token=get_token() if config_store.AUTH_REQUIRED else None)

    def refresh_urls():
        nonlocal qr_url, qr_payload_url
        qr_ip = "127.0.0.1" if QR_FORCE_LOCALHOST else get_effective_ip(config_store.USER_IP)
        qr_url, qr_payload_url = build_urls(qr_ip, port, port, token=get_token() if config_store.AUTH_REQUIRED else None)
        return qr_payload_url

    def get_url_state():
        return {"http_port": port, "ws_port": port, "url": qr_payload_url, "qr_url": qr_url}

    def on_ip_change(new_ip):
        config_store.USER_IP = new_ip
        config_store.save_config()
        refresh_urls()

    dev_mode = os.environ.get("LANVOICE_DEV") in ("1", "true", "yes")
    # 调试模式关闭窗口即退出：禁用 reloader，单进程，不显示托盘
    if dev_mode:
        os.environ["LANVOICE_NO_RELOADER"] = "1"
    dev_close_event = threading.Event() if dev_mode else None
    qr_mgr = QRWindowManager(
        get_user_ip=lambda: config_store.USER_IP,
        on_ip_change=on_ip_change,
        get_effective_ip=lambda: get_effective_ip(config_store.USER_IP),
        get_ports=lambda: (port, port),
        get_payload_url=lambda: qr_payload_url,
        get_config_path=lambda: CONFIG_PATH_IN_USE,
        list_candidates=get_ipv4_candidates,
        dev_mode=dev_mode,
        dev_close_event=dev_close_event,
    )

    print("\n======================================")
    print("✅", _("Started"))
    print("📱", _("Open on phone:"), qr_payload_url)
    print(_("Port:"), port, "(HTTP + WebSocket)")
    print("======================================")
    print("CONFIG(primary):", CONFIG_PATH_PRIMARY)
    print("CONFIG(fallback):", CONFIG_PATH_FALLBACK)
    print("CONFIG(in use):", CONFIG_PATH_IN_USE)
    if config_store.LLM_ENABLED:
        print(_("LLM assist:"), config_store.LLM_MODEL, "@", config_store.LLM_BASE_URL)
    print("======================================\n")

    threading.Thread(target=lambda: run_server(get_url_state), daemon=True).start()

    # Optional: preload LLM model in background when enabled
    if config_store.LLM_ENABLED:
        def _preload_llm():
            try:
                from llm_assistant import preload_model
                if preload_model(config_store.LLM_MODEL, config_store.LLM_BASE_URL):
                    print(f"✅ {_('LLM model preloaded')}: {config_store.LLM_MODEL}")
            except Exception as e:
                print(f"⚠️ {_('LLM preload skipped')}: {e}")
        threading.Thread(target=_preload_llm, daemon=True).start()

    notify(
        _("CursorMobileVoiceInput started"),
        _("Port:{port} (HTTP+WS)\nClick tray to send clipboard\nRight-click tray for QR").format(port=port),
    )
    # ✅ 启动后自动打开二维码窗口（加一点延迟更稳）
    threading.Timer(0.3, qr_mgr.show).start()

    if dev_mode:
        # 调试模式：不显示托盘，仅 QR 窗口；关闭窗口即退出
        dev_close_event.wait()
    else:
        # 仅开发模式+reloader 时：父进程 sleep 避免双托盘；打包 exe 时 dev_mode=False，直接显示托盘
        is_reloader_parent = (
            dev_mode
            and os.environ.get("LANVOICE_NO_RELOADER") != "1"
            and os.environ.get("WERKZEUG_RUN_MAIN") != "true"
        )
        if is_reloader_parent:
            import time
            while True:
                time.sleep(3600)
        run_tray(qr_mgr)


if __name__ == "__main__":
    main()
