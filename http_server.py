"""Unified HTTP + WebSocket server (Flask + Flask-Sock) on a single port, bound to 127.0.0.1 only."""
import json
import threading
from typing import Optional, Set

from flask import Flask, jsonify, send_file
from flask_sock import Sock

from commands import handle_command_with_llm
from notifier import notify
from paths import resource_path
from screenshot import capture_screenshot
from text_handler import handle_text
from input_control import (
    move_mouse,
    move_mouse_rel,
    left_click,
    right_click,
    scroll_mouse,
    focus_target,
    send_unicode_text,
    backspace,
    press_enter,
    press_shift_enter,
    press_arrow,
    press_ctrl_i,
    focus_cursor_and_press_ctrl_i,
    focus_cursor_and_press_ctrl_n,
    read_target_input_content,
)

PORT: Optional[int] = None
CLIENT_COUNT = 0
CLIENT_LOCK = threading.Lock()
WS_CLIENTS: Set = set()
WS_LOCK = threading.Lock()

# Last sync content for preserving @mentions/HTML when pasting back unchanged
_LAST_SYNC_TEXT: str = ""
_LAST_SYNC_HTML: str | None = None


def set_port(port: int):
    global PORT
    PORT = port


def broadcast_json(payload: dict):
    """Send JSON to all connected WebSocket clients."""
    if not WS_CLIENTS:
        return

    data = json.dumps(payload, ensure_ascii=False)
    stale = []
    with WS_LOCK:
        clients = list(WS_CLIENTS)

    for ws in clients:
        try:
            ws.send(data)
        except Exception as e:
            print(f"[broadcast] send failed: {e}")
            stale.append(ws)

    for ws in stale:
        with WS_LOCK:
            WS_CLIENTS.discard(ws)
    if stale:
        print(f"[broadcast] removed stale clients: {len(stale)}")


def schedule_broadcast(payload: dict) -> bool:
    """Schedule broadcast from any thread (e.g. tray)."""
    try:
        broadcast_json(payload)
        return True
    except Exception:
        return False


def _handle_mouse(payload: dict):
    """Handle mouse actions: move, move_rel, left_click, right_click, scroll."""
    action = (payload.get("action") or "").strip()
    x = payload.get("x")
    y = payload.get("y")
    dx = payload.get("dx")
    dy = payload.get("dy")
    delta = payload.get("delta", 0)

    try:
        if action == "move":
            if x is not None and y is not None:
                move_mouse(int(x), int(y))
        elif action == "move_rel":
            if dx is not None and dy is not None:
                move_mouse_rel(int(dx), int(dy))
        elif action == "left_click":
            if x is not None and y is not None:
                move_mouse(int(x), int(y))
            left_click(int(x) if x is not None else None, int(y) if y is not None else None)
        elif action == "right_click":
            if x is not None and y is not None:
                move_mouse(int(x), int(y))
            right_click(int(x) if x is not None else None, int(y) if y is not None else None)
        elif action == "scroll":
            if x is not None and y is not None:
                move_mouse(int(x), int(y))
            scroll_mouse(int(delta), int(x) if x is not None else None, int(y) if y is not None else None)
    except Exception as e:
        print(f"[mouse] error: {e}")


def _handle_key(payload: dict) -> Optional[dict]:
    """Handle key shortcut: @, enter, backspace, arrow keys, ctrl+i, ctrl+n."""
    key = (payload.get("key") or "").strip().lower()
    if not key:
        return None
    try:
        if key not in ("ctrl+i", "ctrl+n"):
            focus_target()
        if key == "@":
            send_unicode_text("@")
        elif key == "enter":
            press_enter()
        elif key == "shift+enter":
            press_shift_enter()
        elif key == "backspace":
            backspace(1)
        elif key in ("up", "down", "left", "right"):
            press_arrow(key)
        elif key == "ctrl+i":
            ok = focus_cursor_and_press_ctrl_i()
            if not ok:
                print("[key] Cursor IDE 窗口未找到")
            return {"ok": ok, "message": "已定位 Cursor 输入框" if ok else "未找到 Cursor IDE 窗口"}
        elif key == "ctrl+n":
            ok = focus_cursor_and_press_ctrl_n()
            if not ok:
                print("[key] Cursor IDE 窗口未找到")
            return {"ok": ok, "message": "已新建 Agent" if ok else "未找到 Cursor IDE 窗口"}
    except Exception as e:
        print(f"[key] error: {e}")
    return None


def create_app(get_url_state):
    """get_url_state returns dict: {http_port, ws_port, url, qr_url} - now http_port==ws_port."""
    app = Flask(__name__)
    sock = Sock(app)

    @app.route("/")
    def index():
        return send_file(resource_path("index.html"))

    @app.route("/config")
    def config():
        state = get_url_state()
        return jsonify(
            {
                "ws_port": state.get("http_port"),
                "http_port": state.get("http_port"),
                "url": state.get("url"),
            }
        )

    @app.route("/api/screenshot")
    def api_screenshot():
        """Return screenshot of all screens as base64 PNG + virtual screen bounds."""
        result = capture_screenshot()
        if result is None:
            return jsonify({"ok": False, "error": "screenshot_failed"}), 500
        b64, bounds = result
        return jsonify({"ok": True, "image": b64, "bounds": bounds})

    @sock.route("/ws")
    def websocket(ws):
        global CLIENT_COUNT, WS_CLIENTS

        with CLIENT_LOCK:
            CLIENT_COUNT += 1
            c = CLIENT_COUNT
        notify("手机已连接", f"连接数：{c}（端口:{PORT}）")
        with WS_LOCK:
            WS_CLIENTS.add(ws)
        print(f"[ws] client connected, total={len(WS_CLIENTS)}")

        try:
            while True:
                msg = ws.receive()
                if msg is None:
                    break

                raw = (msg or "").strip()
                if not raw:
                    continue

                # print("[ws] 收到：", raw)
                msg_type = "text"
                content = raw
                payload = {}
                if raw.startswith("{"):
                    try:
                        payload = json.loads(raw)
                        if isinstance(payload, dict):
                            msg_type = (payload.get("type") or "text").strip()
                            content = payload.get("string")
                    except Exception:
                        msg_type = "text"
                        content = raw

                if msg_type == "cmd":
                    text_cmd = str(content or "").strip()
                    def send_progress(payload: dict):
                        try:
                            ws.send(json.dumps(payload, ensure_ascii=False))
                        except Exception:
                            pass
                    def send_result(payload: dict):
                        try:
                            ws.send(json.dumps(payload, ensure_ascii=False))
                        except Exception:
                            pass
                    handle_command_with_llm(text_cmd, send_progress, send_result)
                elif msg_type == "mouse":
                    _handle_mouse(payload)
                elif msg_type == "key":
                    result = _handle_key(payload)
                    if result is not None:
                        try:
                            ws.send(json.dumps({"type": "key_result", **result}, ensure_ascii=False))
                        except Exception:
                            pass
                elif msg_type == "sync_from_target":
                    try:
                        global _LAST_SYNC_TEXT, _LAST_SYNC_HTML
                        text, html = read_target_input_content()
                        _LAST_SYNC_TEXT = text or ""
                        _LAST_SYNC_HTML = html
                        ws.send(
                            json.dumps(
                                {"type": "sync_content", "string": text or "", "html": html},
                                ensure_ascii=False,
                            )
                        )
                    except Exception as e:
                        print(f"[sync_from_target] error: {e}")
                        try:
                            ws.send(json.dumps({"type": "sync_content", "string": "", "error": str(e)}, ensure_ascii=False))
                        except Exception:
                            pass
                else:
                    replace = bool(payload.get("replace", False))
                    handle_text(
                        str(content or ""),
                        mode="text",
                        replace=replace,
                        last_sync_text=_LAST_SYNC_TEXT if replace else None,
                        last_sync_html=_LAST_SYNC_HTML if replace else None,
                    )

        except Exception:
            pass

        finally:
            with WS_LOCK:
                WS_CLIENTS.discard(ws)
            with CLIENT_LOCK:
                CLIENT_COUNT -= 1
                c = CLIENT_COUNT
            notify("手机已断开", f"连接数：{c}")
            print(f"[ws] client disconnected, total={len(WS_CLIENTS)}")

    return app


def run_server(get_url_state):
    import os

    global PORT
    state = get_url_state()
    port = state.get("http_port")
    set_port(port)

    app = create_app(get_url_state)
    dev_mode = os.environ.get("LANVOICE_DEV") in ("1", "true", "yes")
    use_reloader = dev_mode and os.environ.get("LANVOICE_NO_RELOADER") != "1"
    print(f"HTTP + WebSocket 运行于 http://127.0.0.1:{port} 和 ws://127.0.0.1:{port}/ws")
    if use_reloader:
        print("[dev] 热重载已启用，修改 .py 后自动重启")
    app.run(
        host="127.0.0.1",
        port=port,
        debug=dev_mode,
        use_reloader=use_reloader,
    )
