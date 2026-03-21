# -*- coding: utf-8 -*-
"""
Remote voice client for LAN Voice Input (Windows).

Runs on the machine with the microphone. Hold the configured global hotkey to record,
transcribe with faster-whisper, then send text to the PC running the main server via WebSocket
(same protocol as the phone web client: {"type":"text","string":"..."}).

Config: remote_client_config.json next to this script or next to the packaged .exe
(copy from remote_client_config.example.json).

Whisper weights: by default downloaded to a folder ``whisper_models`` next to the .exe (or script).
Override with ``whisper_models_dir`` in the JSON (relative paths are under the exe directory).

RDP / 远程桌面与热键:
- 麦克风在哪台电脑，客户端就应装在哪台电脑。
- 若在「本机」开着远程桌面窗口且焦点在远程会话里，多数按键会被送给远端，本机钩子可能收不到。
  可行做法：(1) 在远程桌面设置里把「Windows 组合键」设为「应用于此计算机」，并使用带 Win 的组合键；
  (2) 或把本客户端装在远端机上，在远端会话里按热键（此时热键不会被本机 RDP 抢走）。
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from pathlib import Path
from typing import Callable, List, Optional, Set, Tuple
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import numpy as np
import sounddevice as sd
import websocket

# -----------------------------------------------------------------------------
# Paths & config
# -----------------------------------------------------------------------------


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


CONFIG_NAME = "remote_client_config.json"
EXAMPLE_NAME = "remote_client_config.example.json"


def config_path() -> Path:
    return _base_dir() / CONFIG_NAME


def whisper_download_root(cfg: dict) -> Path:
    """Directory for faster-whisper / Hugging Face model cache (default: <exe_dir>/whisper_models)."""
    custom = (cfg.get("whisper_models_dir") or "").strip()
    if custom:
        p = Path(custom)
        if not p.is_absolute():
            p = _base_dir() / p
        return p.resolve()
    return (_base_dir() / "whisper_models").resolve()


def default_config() -> dict:
    return {
        "server_address": "",
        "ws_url": "ws://127.0.0.1:8765/ws",
        "token": "",
        "verify_ssl": False,
        "hotkey": "ctrl+shift+space",
        "whisper_model": "base",
        "whisper_models_dir": "",
        "device": "auto",
        "compute_type": "auto",
        "sample_rate": 16000,
        "language": None,
        "audio_device": None,
    }


def load_config() -> dict:
    path = config_path()
    cfg = default_config()
    if path.is_file():
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                cfg.update(raw)
        except Exception as e:
            print(f"[config] failed to load {path}: {e}")
    return cfg


def save_config(cfg: dict) -> None:
    path = config_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def normalize_ws_url(url: str, token: str) -> str:
    u = (url or "").strip()
    if not u:
        return u
    if u.startswith("http://"):
        u = "ws://" + u[len("http://") :]
    elif u.startswith("https://"):
        u = "wss://" + u[len("https://") :]
    p = urlparse(u)
    path = p.path or ""
    path_norm = path if path.startswith("/") else "/" + path
    if "/ws" not in path_norm.rstrip("/") + "/":
        if not path_norm or path_norm == "/":
            path_norm = "/ws"
        else:
            path_norm = path_norm.rstrip("/") + "/ws"
        p = p._replace(path=path_norm)
        u = urlunparse(p)
    if not token:
        return u
    parsed = urlparse(u)
    q = parse_qs(parsed.query, keep_blank_values=True)
    if "token" not in q or not (q.get("token") or [None])[0]:
        q["token"] = [token]
        new_q = urlencode({k: v[0] if v else "" for k, v in q.items()})
        parsed = parsed._replace(query=new_q)
        u = urlunparse(parsed)
    return u


def parse_server_address_input(raw: str) -> Tuple[str, str]:
    """Split pasted HTTP/HTTPS or WS URL into (ws_url without query, token)."""
    s = (raw or "").strip()
    if not s:
        return "", ""
    final = normalize_ws_url(s, "")
    if not final:
        return "", ""
    p = urlparse(final)
    q = parse_qs(p.query, keep_blank_values=True)
    token = (q.get("token") or [""])[0].strip()
    ws_only = urlunparse((p.scheme, p.netloc, p.path, "", "", ""))
    return ws_only, token


def format_server_address_display(cfg: dict) -> str:
    """Text for the single address field: prefer saved paste string, else derive from ws_url + token."""
    sa = (cfg.get("server_address") or "").strip()
    if sa:
        return sa
    return ws_token_to_browser_paste_url(cfg.get("ws_url") or "", cfg.get("token") or "")


def ws_token_to_browser_paste_url(ws_url: str, token: str) -> str:
    """Build an http(s) URL like the server QR/copy button for display/paste."""
    w = (ws_url or "").strip()
    t = (token or "").strip()
    if not w:
        return ""
    p = urlparse(w)
    scheme = "https" if p.scheme == "wss" else "http"
    base = urlunparse((scheme, p.netloc, "/", "", "", ""))
    if t:
        return f"{base}?{urlencode({'token': t})}"
    return base


def connection_ws_url(cfg: dict) -> str:
    """WebSocket URL for connect(), from one-line paste or legacy ws_url + token."""
    s = (cfg.get("server_address") or "").strip()
    if s:
        return normalize_ws_url(s, "")
    return normalize_ws_url(cfg.get("ws_url") or "", cfg.get("token") or "")


def resolve_device_compute(device: str, compute_type: str) -> Tuple[str, str]:
    dev = (device or "auto").lower().strip()
    ct = (compute_type or "auto").lower().strip()
    if dev == "auto":
        try:
            import ctranslate2

            fn = getattr(ctranslate2, "get_cuda_device_count", None)
            raw = fn() if callable(fn) else 0
            cnt = int(raw) if raw is not None else 0
            dev = "cuda" if cnt > 0 else "cpu"
        except Exception:
            dev = "cpu"
    if ct == "auto":
        ct = "float16" if dev == "cuda" else "int8"
    return dev, ct


# -----------------------------------------------------------------------------
# WebSocket (persistent + recv thread for server ping)
# -----------------------------------------------------------------------------


class WsClient:
    def __init__(self):
        self._lock = threading.Lock()
        self._ws: Optional[websocket.WebSocket] = None
        self._reader_stop = threading.Event()
        self._reader_thread: Optional[threading.Thread] = None
        self.last_error: str = ""

    def close(self) -> None:
        self._reader_stop.set()
        with self._lock:
            if self._ws:
                try:
                    self._ws.close()
                except Exception:
                    pass
                self._ws = None
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2.0)
        self._reader_thread = None
        self._reader_stop.clear()

    def connect(self, url: str, verify_ssl: bool) -> bool:
        self.close()
        sslopt = None
        if url.startswith("wss:"):
            sslopt = {"cert_reqs": ssl.CERT_NONE} if not verify_ssl else {}
        try:
            ws = websocket.create_connection(
                url,
                timeout=15,
                sslopt=sslopt,
                enable_multithread=True,
            )
        except Exception as e:
            self.last_error = str(e)
            return False

        self._reader_stop.clear()

        def reader():
            while not self._reader_stop.is_set():
                try:
                    ws.settimeout(1.0)
                    ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                except Exception:
                    break

        self._reader_thread = threading.Thread(target=reader, daemon=True)
        self._reader_thread.start()
        with self._lock:
            self._ws = ws
        self.last_error = ""
        return True

    def send_text(self, text: str) -> bool:
        payload = json.dumps({"type": "text", "string": text, "replace": False}, ensure_ascii=False)
        with self._lock:
            ws = self._ws
            if not ws:
                return False
            try:
                ws.send(payload)
                return True
            except Exception as e:
                self.last_error = str(e)
                return False


# -----------------------------------------------------------------------------
# Hotkey (pynput) — hold modifiers + key to talk
# -----------------------------------------------------------------------------

_MOD_NAMES = {"ctrl", "control", "shift", "alt", "win", "windows", "cmd"}


def parse_hotkey(spec: str) -> Tuple[Set[str], str]:
    parts = [p.strip().lower() for p in (spec or "").split("+") if p.strip()]
    mods: Set[str] = set()
    main: Optional[str] = None
    for p in parts:
        if p in ("control", "ctrl"):
            mods.add("ctrl")
        elif p == "shift":
            mods.add("shift")
        elif p == "alt":
            mods.add("alt")
        elif p in ("win", "windows", "cmd"):
            mods.add("win")
        else:
            if main is not None:
                raise ValueError("hotkey 只能有一个主键，例如 ctrl+shift+space")
            main = p
    if not main:
        raise ValueError("请指定按键，例如 ctrl+shift+space 或 f9")
    return mods, main


def _key_name(k) -> str:
    try:
        from pynput.keyboard import Key
    except ImportError:
        raise RuntimeError("需要安装 pynput: pip install pynput")

    if k == Key.space:
        return "space"
    if k == Key.enter:
        return "enter"
    if hasattr(k, "char") and k.char:
        return k.char.lower()
    if hasattr(k, "name") and k.name:
        return str(k.name).lower()
    return ""


def _is_mod(k, name: str) -> bool:
    from pynput.keyboard import Key

    if name == "ctrl":
        return k in (Key.ctrl_l, Key.ctrl_r)
    if name == "shift":
        return k in (Key.shift_l, Key.shift_r)
    if name == "alt":
        return k in (Key.alt_l, Key.alt_r, Key.alt_gr)
    if name == "win":
        return k in (Key.cmd, Key.cmd_l, Key.cmd_r)
    return False


def _match_main(k, main: str) -> bool:
    from pynput.keyboard import Key

    m = main.lower().strip()
    if m == "space":
        return k == Key.space
    if m == "enter":
        return k == Key.enter
    if len(m) > 1 and m.startswith("f"):
        try:
            n = int(m[1:])
        except ValueError:
            n = 0
        if 1 <= n <= 24 and hasattr(Key, m):
            return k == getattr(Key, m)
    name = _key_name(k)
    return name == m


class HotkeyHoldController:
    """While modifiers+main are all down, fire on_active True; else False."""

    def __init__(self, hotkey_spec: str, on_active_change: Callable[[bool], None]):
        self._mods, self._main = parse_hotkey(hotkey_spec)
        self._on_active_change = on_active_change
        self._pressed_mods: Set[str] = set()
        self._main_down = False
        self._active = False
        self._listener = None

    def _sync_active(self) -> None:
        mods_ok = self._mods.issubset(self._pressed_mods)
        want = mods_ok and self._main_down
        if want != self._active:
            self._active = want
            self._on_active_change(want)

    def _on_press(self, key) -> None:
        for m in ("ctrl", "shift", "alt", "win"):
            if _is_mod(key, m):
                self._pressed_mods.add(m)
        if _match_main(key, self._main):
            self._main_down = True
        self._sync_active()

    def _on_release(self, key) -> None:
        for m in ("ctrl", "shift", "alt", "win"):
            if _is_mod(key, m):
                self._pressed_mods.discard(m)
        if _match_main(key, self._main):
            self._main_down = False
        self._sync_active()

    def start(self) -> None:
        from pynput.keyboard import Listener

        self.stop()
        self._listener = Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.start()

    def stop(self) -> None:
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None


# -----------------------------------------------------------------------------
# Audio + Whisper
# -----------------------------------------------------------------------------


class VoicePipeline:
    def __init__(self, cfg: dict, status_cb: Callable[[str], None]):
        self.cfg = cfg
        self._status = status_cb
        self._model = None
        self._model_lock = threading.Lock()
        self._recording = threading.Event()
        self._frames: List[np.ndarray] = []
        self._frames_lock = threading.Lock()
        self._stream: Optional[sd.InputStream] = None
        self.sample_rate = int(cfg.get("sample_rate") or 16000)

    def load_model(self) -> None:
        from faster_whisper import WhisperModel

        dev, ct = resolve_device_compute(str(self.cfg.get("device", "auto")), str(self.cfg.get("compute_type", "auto")))
        model_name = (self.cfg.get("whisper_model") or "base").strip()
        root = whisper_download_root(self.cfg)
        root.mkdir(parents=True, exist_ok=True)
        self._status(f"加载 Whisper 模型 {model_name} ({dev}/{ct})，目录 {root}…")
        self._model = WhisperModel(model_name, device=dev, compute_type=ct, download_root=str(root))
        self._status("模型就绪")

    def ensure_model(self) -> bool:
        with self._model_lock:
            if self._model is None:
                try:
                    self.load_model()
                except Exception as e:
                    self._status(f"模型加载失败: {e}")
                    return False
            return True

    def _audio_cb(self, indata, frames, t, status) -> None:
        if status:
            pass
        if self._recording.is_set():
            with self._frames_lock:
                self._frames.append(indata.copy())

    def start_stream(self) -> bool:
        self.stop_stream()
        try:
            device = self.cfg.get("audio_device")
            self._stream = sd.InputStream(
                device=device,
                channels=1,
                samplerate=self.sample_rate,
                dtype="float32",
                callback=self._audio_cb,
                blocksize=1024,
            )
            self._stream.start()
            return True
        except Exception as e:
            self._status(f"麦克风打开失败: {e}")
            return False

    def stop_stream(self) -> None:
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def begin_capture(self) -> None:
        with self._frames_lock:
            self._frames.clear()
        self._recording.set()
        self._status("录音中… (松开热键结束)")

    def end_capture_and_transcribe(self) -> Optional[str]:
        self._recording.clear()
        with self._frames_lock:
            chunks = self._frames
            self._frames = []
        if not chunks:
            self._status("无音频，已忽略")
            return None
        audio = np.concatenate(chunks, axis=0).reshape(-1).astype(np.float32)
        if audio.size < self.sample_rate // 4:
            self._status("录音太短，已忽略")
            return None
        if not self.ensure_model():
            return None
        self._status("识别中…")
        lang = self.cfg.get("language")
        lang = lang if lang else None
        try:
            segments, _info = self._model.transcribe(
                audio,
                language=lang,
                vad_filter=True,
            )
            text = "".join(s.text for s in segments).strip()
        except Exception as e:
            err_l = str(e).lower()
            if any(x in err_l for x in ("onnx", "silero", "no_suchfile", "vad")):
                self._status("VAD 资源不可用，改为无 VAD 识别…")
                try:
                    segments, _info = self._model.transcribe(
                        audio,
                        language=lang,
                        vad_filter=False,
                    )
                    text = "".join(s.text for s in segments).strip()
                except Exception as e2:
                    self._status(f"识别失败: {e2}")
                    return None
            else:
                self._status(f"识别失败: {e}")
                return None
        if not text:
            self._status("未识别到语音")
            return None
        self._status(f"已识别: {text[:48]}{'…' if len(text) > 48 else ''}")
        return text


# -----------------------------------------------------------------------------
# Tk UI
# -----------------------------------------------------------------------------


class App:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Remote Voice Client — faster-whisper")
        self.root.geometry("520x380")
        self.cfg = load_config()
        self.ws = WsClient()
        self.pipeline: Optional[VoicePipeline] = None
        self.hotkey: Optional[HotkeyHoldController] = None
        self._toast_win: Optional[tk.Toplevel] = None
        self._toast_status_lbl: Optional[tk.Label] = None
        self._toast_after_id: Optional[str] = None
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_quit)
        threading.Thread(target=self._bootstrap, daemon=True).start()

    def _build_ui(self) -> None:
        f = ttk.Frame(self.root, padding=10)
        f.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            f,
            text="服务端地址（粘贴服务端窗口「复制服务端地址」或扫码用的完整链接，含 http(s) 与 token）",
        ).grid(row=0, column=0, sticky="w")
        self.var_server = tk.StringVar(value=format_server_address_display(self.cfg))
        ttk.Entry(f, textvariable=self.var_server, width=58).grid(row=1, column=0, sticky="ew", pady=(0, 6))
        row4 = ttk.Frame(f)
        row4.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        self.var_verify = tk.BooleanVar(value=bool(self.cfg.get("verify_ssl", False)))
        ttk.Checkbutton(row4, text="校验 HTTPS 证书", variable=self.var_verify).pack(side=tk.LEFT)
        ttk.Label(row4, text="  热键 (按住说话)").pack(side=tk.LEFT, padx=(16, 4))
        self.var_hotkey = tk.StringVar(value=self.cfg.get("hotkey", "ctrl+shift+space"))
        ttk.Entry(row4, textvariable=self.var_hotkey, width=22).pack(side=tk.LEFT)
        row5 = ttk.Frame(f)
        row5.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(row5, text="模型").pack(side=tk.LEFT)
        self.var_model = tk.StringVar(value=self.cfg.get("whisper_model", "base"))
        ttk.Combobox(
            row5,
            textvariable=self.var_model,
            values=("tiny", "base", "small", "medium", "large-v3"),
            width=12,
            state="readonly",
        ).pack(side=tk.LEFT, padx=(6, 16))
        ttk.Label(row5, text="device").pack(side=tk.LEFT)
        self.var_dev = tk.StringVar(value=self.cfg.get("device", "auto"))
        ttk.Combobox(row5, textvariable=self.var_dev, values=("auto", "cpu", "cuda"), width=8, state="readonly").pack(side=tk.LEFT, padx=6)

        bf = ttk.Frame(f)
        bf.grid(row=4, column=0, sticky="ew", pady=8)
        ttk.Button(bf, text="保存配置", command=self.save_clicked).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(bf, text="连接 WebSocket", command=self.connect_clicked).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(bf, text="复制示例配置到目录", command=self.copy_example).pack(side=tk.LEFT)

        self.status = tk.StringVar(value="启动中…")
        ttk.Label(f, textvariable=self.status, wraplength=480, foreground="#333").grid(row=5, column=0, sticky="w")
        ttk.Label(
            f,
            text="按住热键录音，松开后识别并发送到服务端当前焦点窗口。\n"
            "若焦点在远程桌面里且热键无反应，请把客户端装在麦克风所在机器，或调整远程桌面「Windows 组合键」为「应用于此计算机」。",
            wraplength=500,
            foreground="#666",
            font=("Segoe UI", 9),
        ).grid(row=6, column=0, sticky="w", pady=(12, 0))

        f.columnconfigure(0, weight=1)

    def set_status(self, s: str) -> None:
        def u():
            self.status.set(s)

        self.root.after(0, u)

    def _dismiss_transcript_toast(self) -> None:
        if self._toast_after_id is not None:
            try:
                self.root.after_cancel(self._toast_after_id)
            except Exception:
                pass
            self._toast_after_id = None
        self._toast_status_lbl = None
        if self._toast_win is not None:
            try:
                self._toast_win.destroy()
            except Exception:
                pass
            self._toast_win = None

    def _show_transcript_toast(self, text: str) -> None:
        """Desktop floating preview; call only from Tk main thread."""
        self._dismiss_transcript_toast()
        tw = tk.Toplevel(self.root)
        self._toast_win = tw
        tw.overrideredirect(True)
        tw.attributes("-topmost", True)
        tw.configure(bg="#1e1e1e")
        pad = tk.Frame(tw, bg="#1e1e1e", padx=14, pady=12)
        pad.pack(fill=tk.BOTH, expand=True)
        tk.Label(
            pad,
            text="语音识别",
            font=("Segoe UI", 10, "bold"),
            bg="#1e1e1e",
            fg="#888888",
        ).pack(anchor="w")
        wrap = max(220, min(480, self.root.winfo_screenwidth() - 80))
        tk.Label(
            pad,
            text=text,
            font=("Segoe UI", 12),
            bg="#1e1e1e",
            fg="#f5f5f5",
            wraplength=wrap,
            justify="left",
            anchor="w",
        ).pack(anchor="w", pady=(6, 8))
        self._toast_status_lbl = tk.Label(
            pad,
            text="正在发送…",
            font=("Segoe UI", 9),
            bg="#1e1e1e",
            fg="#7bed9f",
        )
        self._toast_status_lbl.pack(anchor="w")
        tw.update_idletasks()
        w = max(tw.winfo_reqwidth(), 240)
        h = tw.winfo_reqheight()
        sw = tw.winfo_screenwidth()
        sh = tw.winfo_screenheight()
        x = max(8, sw - w - 24)
        y = max(8, sh - h - 88)
        tw.geometry(f"{w}x{h}+{x}+{y}")

    def _finish_transcript_toast(self, ok: bool, err: str) -> None:
        """After WebSocket send; success closes toast, failure shows error then auto-close."""
        if self._toast_win is None:
            return
        try:
            if not self._toast_win.winfo_exists():
                self._toast_win = None
                self._toast_status_lbl = None
                return
        except tk.TclError:
            self._toast_win = None
            self._toast_status_lbl = None
            return
        if ok:
            self._dismiss_transcript_toast()
            return
        if self._toast_status_lbl is not None:
            self._toast_status_lbl.configure(
                text=f"发送失败: {err or '未连接'}（点击浮窗可关闭）",
                fg="#ff6b6b",
            )
        tw = self._toast_win

        def _click_dismiss(_event=None):
            self._dismiss_transcript_toast()

        if tw is not None:
            try:
                tw.bind("<Button-1>", _click_dismiss)
            except Exception:
                pass
        self._toast_after_id = self.root.after(8000, _click_dismiss)

    def _read_ui_cfg(self) -> dict:
        c = load_config()
        server = self.var_server.get().strip()
        c["server_address"] = server
        ws_u, tok = parse_server_address_input(server)
        c["ws_url"] = ws_u
        c["token"] = tok
        c["verify_ssl"] = bool(self.var_verify.get())
        c["hotkey"] = self.var_hotkey.get().strip() or "ctrl+shift+space"
        c["whisper_model"] = self.var_model.get().strip() or "base"
        c["device"] = self.var_dev.get().strip() or "auto"
        return c

    def save_clicked(self) -> None:
        self.cfg = self._read_ui_cfg()
        save_config(self.cfg)
        self.set_status("配置已保存")
        messagebox.showinfo("已保存", f"已写入:\n{config_path()}")

    def copy_example(self) -> None:
        base = _base_dir()
        src = base / EXAMPLE_NAME
        dst = base / CONFIG_NAME
        if not src.is_file():
            messagebox.showerror("缺失", f"未找到 {src}")
            return
        if dst.is_file():
            if not messagebox.askyesno("覆盖？", f"{dst} 已存在，是否覆盖？"):
                return
        import shutil

        shutil.copyfile(src, dst)
        self.cfg = load_config()
        self.var_server.set(format_server_address_display(self.cfg))
        self.set_status(f"已复制 {CONFIG_NAME}")

    def connect_clicked(self) -> None:
        self.cfg = self._read_ui_cfg()
        url = connection_ws_url(self.cfg)
        ok = self.ws.connect(url, verify_ssl=bool(self.cfg.get("verify_ssl")))
        if ok:
            self.set_status(f"WebSocket 已连接: {url.split('?', 1)[0]}")
        else:
            messagebox.showerror("连接失败", self.ws.last_error or "未知错误")

    def _bootstrap(self) -> None:
        self.cfg = self._read_ui_cfg() if hasattr(self, "var_server") else load_config()
        self.pipeline = VoicePipeline(self.cfg, self.set_status)
        if not self.pipeline.start_stream():
            return
        self.pipeline.ensure_model()
        url = connection_ws_url(self.cfg)
        if url:
            ok = self.ws.connect(url, verify_ssl=bool(self.cfg.get("verify_ssl", False)))
            if ok:
                self.set_status("WebSocket 已连接")
            else:
                self.set_status(f"WebSocket 未连接: {self.ws.last_error} — 可稍后点击「连接 WebSocket」")
        self.root.after(0, self._start_hotkey)

    def _on_hold_change(self, active: bool) -> None:
        if not self.pipeline:
            return

        def work():
            if active:
                self.root.after(0, self._dismiss_transcript_toast)
                self.pipeline.begin_capture()
            else:
                text = self.pipeline.end_capture_and_transcribe()
                if text:
                    self.root.after(0, lambda t=text: self._show_transcript_toast(t))
                    ok = self.ws.send_text(text)
                    err = self.ws.last_error or ""
                    self.root.after(0, lambda o=ok, e=err: self._finish_transcript_toast(o, e))
                    if not ok:
                        self.set_status(f"发送失败: {err or '未连接'} — 请点「连接 WebSocket」")

        threading.Thread(target=work, daemon=True).start()

    def _start_hotkey(self) -> None:
        try:
            hk = self.var_hotkey.get().strip() if hasattr(self, "var_hotkey") else self.cfg.get("hotkey", "")
            self.hotkey = HotkeyHoldController(hk, self._on_hold_change)
            self.hotkey.start()
            self.set_status(f"热键就绪: 按住 {hk} 说话")
        except Exception as e:
            self.set_status(f"热键注册失败: {e}")
            messagebox.showerror("热键", str(e))

    def on_quit(self) -> None:
        self._dismiss_transcript_toast()
        if self.hotkey:
            self.hotkey.stop()
        if self.pipeline:
            self.pipeline.stop_stream()
        self.ws.close()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass
    if not config_path().is_file() and (_base_dir() / EXAMPLE_NAME).is_file():
        pass
    App().run()


if __name__ == "__main__":
    main()
