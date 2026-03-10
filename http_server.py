"""Unified HTTP + WebSocket server (Flask + Flask-Sock) on a single port, bound to 127.0.0.1 only."""
import json
import threading
from typing import Optional, Set

from flask import Flask, jsonify, send_file
from flask_sock import Sock

from commands import execute_command, match_command
from notifier import notify
from paths import resource_path
from text_handler import handle_text

PORT: Optional[int] = None
CLIENT_COUNT = 0
CLIENT_LOCK = threading.Lock()
WS_CLIENTS: Set = set()
WS_LOCK = threading.Lock()


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

                print("[ws] 收到：", raw)
                msg_type = "text"
                content = raw
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
                    if match_command(text_cmd):
                        result = execute_command(text_cmd)
                        resp = {
                            "type": "cmd_result",
                            "string": text_cmd,
                            "ok": bool(result.output.get("ok")) if isinstance(result.output, dict) else False,
                            "message": result.output.get("message") if isinstance(result.output, dict) else result.display_text,
                        }
                        ws.send(json.dumps(resp, ensure_ascii=False))
                    else:
                        handle_text(text_cmd, mode="cmd")
                else:
                    handle_text(str(content or ""), mode="text")

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
    global PORT
    state = get_url_state()
    port = state.get("http_port")
    set_port(port)

    app = create_app(get_url_state)
    print(f"HTTP + WebSocket 运行于 http://127.0.0.1:{port} 和 ws://127.0.0.1:{port}/ws")
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
