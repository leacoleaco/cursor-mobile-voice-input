"""Tkinter QR window with IP selection and Ollama settings."""
import os
import queue
import threading
from typing import Callable, List, Optional, Tuple

import qrcode
import tkinter as tk
from PIL import Image, ImageTk
from tkinter import ttk

import config_store
from i18n import _


class QRWindowManager:
    """Run a single Tk mainloop and expose thread-safe show/close/call APIs."""

    def __init__(
        self,
        get_user_ip: Callable[[], Optional[str]],
        on_ip_change: Callable[[Optional[str]], None],
        get_effective_ip: Callable[[], str],
        get_ports: Callable[[], Tuple[int, int]],
        get_payload_url: Callable[[], str],
        get_config_path: Callable[[], str],
        list_candidates: Callable[[], List[Tuple[str, str]]],
        *,
        on_locale_change: Optional[Callable[[], None]] = None,
        dev_mode: bool = False,
        dev_close_event: Optional[threading.Event] = None,
    ):
        self.get_user_ip = get_user_ip
        self.on_ip_change = on_ip_change
        self.on_locale_change = on_locale_change
        self.get_effective_ip = get_effective_ip
        self.get_ports = get_ports
        self.get_payload_url = get_payload_url
        self.get_config_path = get_config_path
        self.list_candidates = list_candidates
        self.dev_mode = dev_mode
        self.dev_close_event = dev_close_event

        self.cmd_q = queue.Queue()
        self.thread = threading.Thread(target=self._tk_thread, daemon=True)
        self.thread.start()

    def show(self):
        self.cmd_q.put(("show", None))

    def close(self):
        self.cmd_q.put(("close", None))

    def call(self, fn):
        self.cmd_q.put(("call", fn))

    def _tk_thread(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("QRRoot")

        self.top = None
        self.tk_img = None

        self.ip_items: List[Tuple[str, str]] = []
        self.ip_var = tk.StringVar()
        self.combo = None

        self.img_label = None
        self.url_label = None
        self.tip_label = None
        self.lang_combo = None
        self.lang_var = None
        self._header_widgets = {}  # for refresh

        self.root.after(100, self._poll_queue)
        self.root.mainloop()

    def _poll_queue(self):
        try:
            while True:
                cmd, data = self.cmd_q.get_nowait()
                if cmd == "show":
                    self._show_window()
                elif cmd == "close":
                    self._close_window()
                elif cmd == "call":
                    try:
                        data()
                    except Exception:
                        pass
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _close_window(self):
        if self.top is not None:
            try:
                self.top.destroy()
            except Exception:
                pass
        self.top = None
        self.tk_img = None
        self.combo = None
        self.lang_combo = None
        self.lang_var = None
        self._header_widgets = None
        self.img_label = None
        self.url_label = None
        self.tip_label = None

        if self.dev_mode:
            self.root.quit()
            if self.dev_close_event:
                self.dev_close_event.set()
            os._exit(0)

    def _ensure_window(self):
        if self.top is not None:
            return

        self.top = tk.Toplevel(self.root)
        self.top.title(_("Scan QR to open voice input page"))
        self.top.attributes("-topmost", True)
        self.top.protocol("WM_DELETE_WINDOW", self._close_window)

        header = ttk.Frame(self.top)
        header.pack(fill="x", padx=10, pady=(10, 6))

        self._header_widgets = {}
        self._header_widgets["ip_label"] = ttk.Label(header, text=_("Select NIC/IP:"))
        self._header_widgets["ip_label"].pack(side="left")

        self.combo = ttk.Combobox(header, textvariable=self.ip_var, state="readonly", width=40)
        self.combo.pack(side="left", padx=6, fill="x", expand=True)

        _lang_display = {"zh_CN": "中文", "en": "English"}
        _lang_value = {"中文": "zh_CN", "English": "en"}
        self._lang_display = _lang_display
        self._lang_value = _lang_value
        self.lang_var = tk.StringVar(value=_lang_display.get(config_store.LOCALE or "zh_CN", "中文"))
        self.lang_combo = ttk.Combobox(header, textvariable=self.lang_var, values=["中文", "English"], state="readonly", width=8)
        self.lang_combo.pack(side="left", padx=(0, 6))
        self.lang_combo.bind("<<ComboboxSelected>>", lambda e: self._on_lang_changed())

        btn_auto = ttk.Button(header, text=_("Auto recommended"), command=self._on_auto_ip)
        btn_auto.pack(side="left", padx=(6, 0))
        self._header_widgets["btn_auto"] = btn_auto

        btn_settings = ttk.Button(header, text=_("Settings"), command=self._show_settings)
        btn_settings.pack(side="left", padx=(6, 0))
        self._header_widgets["btn_settings"] = btn_settings

        self.combo.bind("<<ComboboxSelected>>", lambda e: self._on_ip_selected())

        self.img_label = ttk.Label(self.top)
        self.img_label.pack(padx=10, pady=10)

        self.url_label = ttk.Label(self.top, font=("Arial", 12))
        self.url_label.pack(padx=10, pady=(0, 6))

        self.tip_label = ttk.Label(self.top, font=("Arial", 10), foreground="#333", justify="center")
        self.tip_label.pack(padx=10, pady=(0, 10))

    def _reload_ip_list_and_select_current(self):
        current = (self.get_user_ip() or "").strip()

        self.ip_items = self.list_candidates()
        labels = [lbl for (lbl, _ip) in self.ip_items]
        self.combo["values"] = labels

        idx = 0
        if current:
            for i, (_lbl, ip) in enumerate(self.ip_items):
                if ip == current:
                    idx = i
                    break
            else:
                self.on_ip_change(None)  # invalid current -> reset
                current = ""

        if not current:
            auto_prefix = _("Auto recommended")
            for i, (lbl, _ip) in enumerate(self.ip_items):
                if lbl.startswith(auto_prefix):
                    idx = i
                    break

        if labels:
            self.combo.current(idx)
            self.ip_var.set(labels[idx])
        if self.lang_var and self.lang_combo:
            self.lang_var.set(self._lang_display.get(config_store.LOCALE or "zh_CN", "中文"))

    def _selected_ip(self) -> str:
        label = self.ip_var.get()
        for (lbl, ip) in self.ip_items:
            if lbl == label:
                return ip
        return self.get_effective_ip()

    def _on_ip_selected(self):
        ip = self._selected_ip()
        self.on_ip_change(ip)
        self._refresh_qr_and_text()

    def _on_auto_ip(self):
        self.on_ip_change(None)
        self._reload_ip_list_and_select_current()
        self._refresh_qr_and_text()

    def _on_lang_changed(self):
        """Apply language change immediately."""
        display = (self.lang_var.get() or "中文").strip()
        locale = self._lang_value.get(display, "zh_CN")
        config_store.LOCALE = locale
        config_store.save_config()
        from i18n import set_locale
        set_locale(locale)
        if self.on_locale_change:
            self.on_locale_change()
        self._refresh_ui_text()

    def _refresh_ui_text(self):
        """Refresh all translatable text in the window."""
        if not self.top or not self._header_widgets:
            return
        self.top.title(_("Scan QR to open voice input page"))
        self._header_widgets["ip_label"].configure(text=_("Select NIC/IP:"))
        self._header_widgets["btn_auto"].configure(text=_("Auto recommended"))
        self._header_widgets["btn_settings"].configure(text=_("Settings"))
        self._reload_ip_list_and_select_current()
        self._refresh_qr_and_text()

    def _show_settings(self):
        """Open Ollama settings dialog."""
        dlg = tk.Toplevel(self.top if self.top else self.root)
        dlg.title(_("Ollama Settings"))
        dlg.transient(self.top if self.top else self.root)
        dlg.grab_set()
        dlg.resizable(False, False)

        f = ttk.Frame(dlg, padding=12)
        f.pack(fill="both", expand=True)

        # Enable checkbox
        var_enabled = tk.BooleanVar(value=config_store.LLM_ENABLED)
        cb = ttk.Checkbutton(f, text=_("Enable Ollama for command matching"), variable=var_enabled)
        cb.pack(anchor="w", pady=(0, 10))

        # Base URL
        ttk.Label(f, text=_("Ollama URL:")).pack(anchor="w")
        entry_url = ttk.Entry(f, width=42)
        entry_url.insert(0, config_store.LLM_BASE_URL or "http://127.0.0.1:11434")
        entry_url.pack(fill="x", pady=(2, 8))

        # Model
        ttk.Label(f, text=_("Model:")).pack(anchor="w")
        entry_model = ttk.Entry(f, width=42)
        entry_model.insert(0, config_store.LLM_MODEL or "qwen3.5:0.8b")
        entry_model.pack(fill="x", pady=(2, 12))

        def on_ok():
            url = (entry_url.get() or "").strip() or "http://127.0.0.1:11434"
            model = (entry_model.get() or "").strip() or "qwen3.5:0.8b"
            config_store.LLM_ENABLED = var_enabled.get()
            config_store.LLM_BASE_URL = url
            config_store.LLM_MODEL = model
            config_store.save_config()
            from i18n import set_locale
            set_locale(locale)
            dlg.destroy()

        def on_cancel():
            dlg.destroy()

        btn_f = ttk.Frame(f)
        btn_f.pack(fill="x", pady=(4, 0))
        ttk.Button(btn_f, text=_("OK"), command=on_ok).pack(side="left", padx=(0, 6))
        ttk.Button(btn_f, text=_("Cancel"), command=on_cancel).pack(side="left")

        dlg.update_idletasks()
        dlg.geometry(f"+{dlg.winfo_screenwidth()//2 - dlg.winfo_reqwidth()//2}+{dlg.winfo_screenheight()//2 - dlg.winfo_reqheight()//2}")

    def _refresh_qr_and_text(self):
        url = self.get_payload_url() or ""
        if not url:
            return

        qr = qrcode.QRCode(box_size=8, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        self.tk_img = ImageTk.PhotoImage(img)

        self.img_label.configure(image=self.tk_img)
        self.url_label.configure(text=url)

        ip_show = self.get_effective_ip()
        mode = _("Manual") if (self.get_user_ip() and self.get_user_ip().strip()) else _("Auto")
        http_port, ws_port = self.get_ports()
        llm_line = _("LLM: {model} (enabled)").format(model=config_store.LLM_MODEL) if config_store.LLM_ENABLED else _("LLM: disabled")
        close_tip = _("Closing this window will exit the app") if self.dev_mode else _("Closing this window does not affect background running")
        self.tip_label.configure(
            text=_("Scan with phone to open page (same WiFi / same subnet)") + "\n"
            + _("Mode: {mode}  IP: {ip}").format(mode=mode, ip=ip_show) + "\n"
            + _("HTTP:{port}  WS:{port}").format(port=http_port) + "\n"
            + llm_line + "\n"
            + close_tip + "\n"
            + _("Config file: {path}").format(path=self.get_config_path())
        )

    def _show_window(self):
        self._ensure_window()

        try:
            self.top.deiconify()
            self.top.lift()
            self.top.attributes("-topmost", True)
            self.top.after(200, lambda: self.top.attributes("-topmost", False))
        except Exception:
            pass

        self._reload_ip_list_and_select_current()
        self._refresh_qr_and_text()
