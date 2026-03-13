"""Unified HTTP + WebSocket server (Flask + Flask-Sock) on a single port, bound to 127.0.0.1 only."""
import json
import threading
from typing import Optional, Set

from flask import Flask, Response, jsonify, request, send_file
from flask_sock import Sock

from auth_token import get_token, validate_request
from i18n import _
from commands import handle_command_with_llm
from web_i18n import WEB_TRANSLATIONS


def _auth_error_html() -> str:
    """HTML page shown when auth fails on main page (e.g. old QR / wrong token)."""
    title = _("Permission error")
    heading = _("Invalid permission")
    msg = _("Please scan again")
    btn = _("I understand")
    alert_msg = _("Close this page and scan the latest QR code")
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>{title}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: sans-serif; min-height: 100vh; display: flex; align-items: center; justify-content: center; background: rgba(0,0,0,0.4); padding: 20px; }}
    .dialog {{ background: #fff; border-radius: 14px; padding: 28px; max-width: 320px; text-align: center; box-shadow: 0 8px 32px rgba(0,0,0,0.2); }}
    .dialog h3 {{ margin: 0 0 14px; font-size: 20px; color: #c0392b; }}
    .dialog p {{ margin: 0 0 24px; font-size: 16px; color: #555; line-height: 1.6; }}
    .dialog .btn {{ font-size: 16px; padding: 12px 24px; border-radius: 10px; border: none; background: #4a90d9; color: #fff; cursor: pointer; }}
    .dialog .btn:active {{ transform: translateY(1px); }}
  </style>
</head>
<body>
  <div class="dialog">
    <h3>⚠️ {heading}</h3>
    <p>{msg}</p>
    <button type="button" class="btn" onclick="alert('{alert_msg}')">{btn}</button>
  </div>
</body>
</html>"""

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
    press_key_combo,
)

PORT: Optional[int] = None
CLIENT_COUNT = 0
CLIENT_LOCK = threading.Lock()
WS_CLIENTS: Set = set()
WS_LOCK = threading.Lock()
_ON_CLIENT_COUNT_CHANGE = None  # Callable[[int], None]


def get_connection_count() -> int:
    """Return the current number of active WebSocket connections."""
    with WS_LOCK:
        return len(WS_CLIENTS)


def set_on_client_count_change(callback):
    """Register a callback invoked (from a bg thread) whenever connection count changes."""
    global _ON_CLIENT_COUNT_CHANGE
    _ON_CLIENT_COUNT_CHANGE = callback

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
    """Handle key shortcut: single chars, combos (ctrl+a, ctrl+shift+z), ctrl+i, ctrl+n."""
    key = (payload.get("key") or "").strip().lower()
    if not key:
        return None
    try:
        if key == "ctrl+i":
            ok = focus_cursor_and_press_ctrl_i()
            if not ok:
                print("[key] Cursor IDE 窗口未找到")
            return {"ok": ok, "message": _("Cursor input focused") if ok else _("Cursor IDE window not found")}
        if key == "ctrl+n":
            ok = focus_cursor_and_press_ctrl_n()
            if not ok:
                print("[key] Cursor IDE 窗口未找到")
            return {"ok": ok, "message": _("New Agent created") if ok else _("Cursor IDE window not found")}
        press_key_combo(key)
    except Exception as e:
        print(f"[key] error: {e}")
    return None


def create_app(get_url_state):
    """get_url_state returns dict: {http_port, ws_port, url, qr_url} - now http_port==ws_port."""
    app = Flask(__name__)
    # Send WebSocket ping every 20s so tunnel routers don't silently drop idle connections.
    # The client will respond with pong automatically; if it doesn't, the connection is closed
    # and the client's onclose fires, triggering the retry logic.
    app.config["SOCK_SERVER_OPTIONS"] = {"ping_interval": 20}
    sock = Sock(app)

    @app.before_request
    def _require_auth():
        """Require Authorization/X-Authority/X-Token header or token query param for all routes."""
        token = get_token()
        if not token:
            return None  # No auth configured
        auth = request.headers.get("Authorization")
        x_authority = request.headers.get("X-Authority")
        x_token = request.headers.get("X-Token")
        q_token = request.args.get("token")
        if not validate_request(auth, x_authority, x_token, q_token):
            # Main page: return HTML error so user sees friendly message
            if request.path == "/" or request.path == "":
                return Response(_auth_error_html(), status=401, mimetype="text/html; charset=utf-8")
            return jsonify({"error": "unauthorized", "message": "Missing or invalid token"}), 401

    @app.route("/")
    def index():
        return send_file(resource_path("index.html"))

    @app.route("/config")
    def config():
        import config_store
        state = get_url_state()
        return jsonify(
            {
                "ws_port": state.get("http_port"),
                "http_port": state.get("http_port"),
                "url": state.get("url"),
                "ws_url": state.get("ws_url"),
                "locale": config_store.LOCALE,
            }
        )

    @app.route("/api/i18n")
    def api_i18n():
        import config_store
        lang = request.args.get("lang") or config_store.LOCALE or "zh_CN"
        if lang not in WEB_TRANSLATIONS:
            lang = "zh_CN"
        return jsonify(WEB_TRANSLATIONS[lang])

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
        with WS_LOCK:
            WS_CLIENTS.add(ws)
        _count = len(WS_CLIENTS)
        notify(_("Phone connected"), _("Connections: {c} (port:{port})").format(c=_count, port=PORT))
        print(f"[ws] client connected, total={_count}")
        if _ON_CLIENT_COUNT_CHANGE:
            try:
                _ON_CLIENT_COUNT_CHANGE(_count)
            except Exception:
                pass

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
                elif msg_type in ("text", "html"):
                    try:
                        text_str = str(content or "").strip() if content is not None else ""
                        replace = bool(payload.get("replace", False))
                        handle_text(
                            text_str,
                            mode=msg_type,
                            replace=replace,
                            last_sync_text=_LAST_SYNC_TEXT or None,
                            last_sync_html=_LAST_SYNC_HTML,
                        )
                    except Exception as e:
                        print(f"[text/html] error: {e}")
                else:
                    err_msg = _("Unknown message type")
                    err_html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/></head>
<body style="margin:0;font-family:sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#f5f5f5;padding:20px;">
  <div style="background:#fff;border-radius:14px;padding:28px;max-width:320px;text-align:center;box-shadow:0 8px 32px rgba(0,0,0,0.1);">
    <p style="margin:0;font-size:16px;color:#c0392b;">⚠️ {err_msg}</p>
  </div>
</body>
</html>"""
                    try:
                        ws.send(json.dumps({"type": "unknown_msg_type", "message": err_msg, "html": err_html}, ensure_ascii=False))
                    except Exception:
                        pass

        except Exception:
            pass

        finally:
            with WS_LOCK:
                WS_CLIENTS.discard(ws)
            with CLIENT_LOCK:
                CLIENT_COUNT -= 1
                c = CLIENT_COUNT
            _count = len(WS_CLIENTS)
            notify(_("Phone disconnected"), _("Connections: {c}").format(c=_count))
            print(f"[ws] client disconnected, total={_count}")
            if _ON_CLIENT_COUNT_CHANGE:
                try:
                    _ON_CLIENT_COUNT_CHANGE(_count)
                except Exception:
                    pass

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
    print(f"HTTP + WebSocket 运行于 http://0.0.0.0:{port} 和 ws://0.0.0.0:{port}/ws")
    if use_reloader:
        print("[dev] 热重载已启用，修改 .py 后自动重启")
    # Bind to 0.0.0.0 so SSH tunnel (and LAN) can connect; 127.0.0.1 can reject tunnel on some systems.
    # threaded=True is critical: each WebSocket connection blocks a thread; without it only one
    # connection can be handled at a time, causing new clients to hang until the previous thread exits.
    app.run(
        host="0.0.0.0",
        port=port,
        debug=dev_mode,
        use_reloader=use_reloader,
        threaded=True,
    )
