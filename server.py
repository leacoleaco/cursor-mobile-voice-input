# server.py
# -*- coding: utf-8 -*-
"""Main entry point for LAN Voice Input (modularized)."""
import asyncio
import os
import sys
import threading

# Fix Windows console encoding for emoji/Unicode (GBK cannot encode emoji)
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import config_store
from config_store import CONFIG_PATH_FALLBACK, CONFIG_PATH_IN_USE, CONFIG_PATH_PRIMARY
from http_server import run_server
from ip_utils import build_urls, choose_free_port, get_effective_ip, get_ipv4_candidates
from notifier import notify
from qr_window import QRWindowManager
from settings import DEFAULT_HTTP_PORT, QR_FORCE_LOCALHOST
from tray_app import run_tray


def main():
    # ✅ 启动即读取/创建 config（打包后优先 exe 同级 config.json）
    config_store.load_config()

    port = choose_free_port(DEFAULT_HTTP_PORT)

    qr_ip = "127.0.0.1" if QR_FORCE_LOCALHOST else get_effective_ip(config_store.USER_IP)
    qr_url, qr_payload_url = build_urls(qr_ip, port, port)

    def refresh_urls():
        nonlocal qr_url, qr_payload_url
        qr_ip = "127.0.0.1" if QR_FORCE_LOCALHOST else get_effective_ip(config_store.USER_IP)
        qr_url, qr_payload_url = build_urls(qr_ip, port, port)
        return qr_payload_url

    def get_url_state():
        return {"http_port": port, "ws_port": port, "url": qr_payload_url, "qr_url": qr_url}

    def on_ip_change(new_ip):
        config_store.USER_IP = new_ip
        config_store.save_config()
        refresh_urls()

    qr_mgr = QRWindowManager(
        get_user_ip=lambda: config_store.USER_IP,
        on_ip_change=on_ip_change,
        get_effective_ip=lambda: get_effective_ip(config_store.USER_IP),
        get_ports=lambda: (port, port),
        get_payload_url=lambda: qr_payload_url,
        get_config_path=lambda: CONFIG_PATH_IN_USE,
        list_candidates=get_ipv4_candidates,
    )

    print("\n======================================")
    print("✅ 已启动")
    print("📱 手机打开：", qr_payload_url)
    print("端口:", port, "(HTTP + WebSocket 共用)")
    print("======================================")
    print("CONFIG(primary):", CONFIG_PATH_PRIMARY)
    print("CONFIG(fallback):", CONFIG_PATH_FALLBACK)
    print("CONFIG(in use):", CONFIG_PATH_IN_USE)
    if config_store.LLM_ENABLED:
        print("LLM 辅助:", config_store.LLM_MODEL, "@", config_store.LLM_BASE_URL, "(命令模糊匹配)")
    print("======================================\n")

    threading.Thread(target=lambda: run_server(get_url_state), daemon=True).start()

    # Optional: preload LLM model in background when enabled
    if config_store.LLM_ENABLED:
        def _preload_llm():
            try:
                from llm_assistant import preload_model
                if preload_model(config_store.LLM_MODEL, config_store.LLM_BASE_URL):
                    print(f"✅ LLM 模型已预加载: {config_store.LLM_MODEL}")
            except Exception as e:
                print(f"⚠️ LLM 预加载跳过: {e}")
        threading.Thread(target=_preload_llm, daemon=True).start()

    notify(
        "LANVoiceInput 启动成功",
        f"端口:{port} (HTTP+WS)\n单击托盘图标快速发送剪贴板到网页\n右键托盘菜单可显示二维码",
    )
    # ✅ 启动后自动打开二维码窗口（加一点延迟更稳）
    threading.Timer(0.3, qr_mgr.show).start()

    # 开发模式：Flask reloader 会 spawn 子进程，父进程不显示托盘，避免双托盘
    if os.environ.get("LANVOICE_DEV") in ("1", "true", "yes") and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        import time
        while True:
            time.sleep(3600)
    run_tray(qr_mgr)


if __name__ == "__main__":
    main()
