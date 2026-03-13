"""Tkinter QR window with IP selection and Ollama settings."""
import os
import queue
import threading
from typing import Callable, List, Optional, Tuple

import qrcode
import tkinter as tk
from PIL import Image, ImageTk
from tkinter import ttk, messagebox

import config_store
from i18n import _

ACCESS_MODE_LOCAL = "local"
ACCESS_MODE_LAN = "lan"
ACCESS_MODE_PUBLIC = "public"


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
        build_url_for_ip: Optional[Callable[[str], str]] = None,
        ssh_tunnel=None,
        on_locale_change: Optional[Callable[[], None]] = None,
        get_connection_count: Optional[Callable[[], int]] = None,
        dev_mode: bool = False,
        dev_close_event: Optional[threading.Event] = None,
    ):
        self.get_user_ip = get_user_ip
        self.on_ip_change = on_ip_change
        self.on_locale_change = on_locale_change
        self.get_effective_ip = get_effective_ip
        self.get_ports = get_ports
        self.get_payload_url = get_payload_url
        self.build_url_for_ip = build_url_for_ip
        self.get_config_path = get_config_path
        self.list_candidates = list_candidates
        self.ssh_tunnel = ssh_tunnel
        self.get_connection_count = get_connection_count
        self.dev_mode = dev_mode
        self.dev_close_event = dev_close_event

        self.cmd_q = queue.Queue()
        self.log_q = queue.Queue()
        self.thread = threading.Thread(target=self._tk_thread, daemon=True)
        self.thread.start()

    def log(self, msg: str):
        """Thread-safe: append message to log area."""
        if msg:
            self.log_q.put(str(msg))

    def show(self):
        self.cmd_q.put(("show", None))

    def close(self):
        self.cmd_q.put(("close", None))

    def call(self, fn):
        self.cmd_q.put(("call", fn))

    def refresh_qr(self):
        """Refresh QR and tip (call from Tk thread or via call())."""
        if self.top and self.img_label:
            self._refresh_qr_and_text()

    def update_connection_count(self, count: int):
        """Thread-safe: update the connection count display."""
        self.cmd_q.put(("conn_count", count))

    def _tk_thread(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("QRRoot")

        self.top = None
        self.tk_img = None

        self.ip_items: List[Tuple[str, str]] = []
        self.ip_var = tk.StringVar()
        self.combo = None

        self.access_mode_var = tk.StringVar(value=ACCESS_MODE_LAN)
        self._access_radio_btns = {}  # label -> Radiobutton widget
        self._mode_row = None
        self._lan_row = None

        self.img_label = None
        self.url_label = None
        self.tip_label = None
        self.conn_label = None
        self.log_text = None
        self.lang_combo = None
        self.lang_var = None
        self._header_widgets = {}  # for refresh
        self._loading_after_id = None
        self._loading_dot_count = 0

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
                elif cmd == "conn_count":
                    self._update_conn_label(data)
        except queue.Empty:
            pass
        while True:
            try:
                msg = self.log_q.get_nowait()
                self._append_log(msg)
            except queue.Empty:
                break
        self.root.after(100, self._poll_queue)

    def _update_conn_label(self, count: int):
        """Update connection count label in the window (called from Tk thread)."""
        if self.top and self.conn_label:
            try:
                color = "#2ecc71" if count > 0 else "#888"
                self.conn_label.configure(
                    text=_("Connected devices: {n}").format(n=count),
                    foreground=color,
                )
            except Exception:
                pass

    def _append_log(self, msg: str):
        if self.top and hasattr(self, "log_text") and self.log_text:
            try:
                self.log_text.configure(state="normal")
                self.log_text.insert("end", msg + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
            except Exception:
                pass

    def _clear_log(self):
        if hasattr(self, "log_text") and self.log_text:
            try:
                self.log_text.configure(state="normal")
                self.log_text.delete("1.0", "end")
                self.log_text.configure(state="disabled")
            except Exception:
                pass

    def _close_window(self):
        self._stop_loading_animation()
        if self.dev_mode:
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
            self._access_radio_btns = {}
            self._mode_row = None
            self._lan_row = None
            self.img_label = None
            self.url_label = None
            self.tip_label = None
            self.conn_label = None
            self.log_text = None
            self.root.quit()
            if self.dev_close_event:
                self.dev_close_event.set()
            os._exit(0)
            return

        if not config_store.RUN_IN_BACKGROUND:
            from tray_app import request_quit
            request_quit()
            return

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
        self._access_radio_btns = {}
        self._mode_row = None
        self._lan_row = None
        self.img_label = None
        self.url_label = None
        self.tip_label = None
        self.conn_label = None
        self.log_text = None

    def _ensure_window(self):
        if self.top is not None:
            return

        self.top = tk.Toplevel(self.root)
        self.top.title(_("Scan QR to open voice input page"))
        self.top.attributes("-topmost", True)
        self.top.protocol("WM_DELETE_WINDOW", self._close_window)
        self.top.resizable(True, True)

        # ── Row 1: access mode radio buttons ──────────────────────────────────
        mode_row = ttk.Frame(self.top)
        mode_row.pack(fill="x", padx=10, pady=(10, 2))
        self._mode_row = mode_row

        ttk.Label(mode_row, text=_("Access mode:"), font=("Arial", 10, "bold")).pack(side="left", padx=(0, 8))
        self._access_radio_btns = {}
        for mode_value, label_key in (
            (ACCESS_MODE_LOCAL, "Local (this machine only)"),
            (ACCESS_MODE_LAN,   "LAN (same network)"),
            (ACCESS_MODE_PUBLIC, "Public (internet)"),
        ):
            rb = ttk.Radiobutton(
                mode_row, text=_(label_key),
                variable=self.access_mode_var, value=mode_value,
                command=self._on_access_mode_changed,
            )
            rb.pack(side="left", padx=(0, 12))
            self._access_radio_btns[mode_value] = rb

        # ── Row 2: LAN IP selector (hidden when mode != lan) ──────────────────
        self._lan_row = ttk.Frame(self.top)
        self._lan_row.pack(fill="x", padx=10, pady=(2, 2))

        self._header_widgets = {}
        self._header_widgets["ip_label"] = ttk.Label(self._lan_row, text=_("Select NIC/IP:"))
        self._header_widgets["ip_label"].pack(side="left")

        self.combo = ttk.Combobox(self._lan_row, textvariable=self.ip_var, state="readonly", width=40)
        self.combo.pack(side="left", padx=6, fill="x", expand=True)
        self.combo.bind("<<ComboboxSelected>>", lambda e: self._on_ip_selected())

        btn_auto = ttk.Button(self._lan_row, text=_("Auto recommended"), command=self._on_auto_ip)
        btn_auto.pack(side="left", padx=(6, 0))
        self._header_widgets["btn_auto"] = btn_auto

        # ── Row 3: settings / language / background ───────────────────────────
        toolbar = ttk.Frame(self.top)
        toolbar.pack(fill="x", padx=10, pady=(2, 6))

        _lang_display = {"zh_CN": "中文", "en": "English"}
        _lang_value = {"中文": "zh_CN", "English": "en"}
        self._lang_display = _lang_display
        self._lang_value = _lang_value
        self.lang_var = tk.StringVar(value=_lang_display.get(config_store.LOCALE or "zh_CN", "中文"))
        self.lang_combo = ttk.Combobox(toolbar, textvariable=self.lang_var, values=["中文", "English"], state="readonly", width=8)
        self.lang_combo.pack(side="left", padx=(0, 6))
        self.lang_combo.bind("<<ComboboxSelected>>", lambda e: self._on_lang_changed())

        btn_settings = ttk.Button(toolbar, text=_("Settings"), command=self._show_settings)
        btn_settings.pack(side="left", padx=(0, 6))
        self._header_widgets["btn_settings"] = btn_settings

        # SSH tunnel config button (always available for public mode)
        self.btn_tunnel_cfg = ttk.Button(toolbar, text=_("Tunnel config"), command=self._show_tunnel_settings)
        self.btn_tunnel_cfg.pack(side="left", padx=(0, 6))
        self._header_widgets["btn_tunnel_cfg"] = self.btn_tunnel_cfg
        if not self.ssh_tunnel:
            self.btn_tunnel_cfg.pack_forget()

        # Kill remote port button — helps recover when server port is occupied
        self.btn_kill_port = ttk.Button(toolbar, text=_("Kill remote port"), command=self._on_kill_remote_port)
        self.btn_kill_port.pack(side="left", padx=(0, 6))
        self._header_widgets["btn_kill_port"] = self.btn_kill_port
        if not self.ssh_tunnel:
            self.btn_kill_port.pack_forget()

        self.run_in_bg_var = tk.BooleanVar(value=config_store.RUN_IN_BACKGROUND)
        cb_bg = ttk.Checkbutton(toolbar, text=_("Run in background when closed"), variable=self.run_in_bg_var, command=self._on_run_in_bg_changed)
        cb_bg.pack(side="left", padx=(6, 0))
        self._header_widgets["cb_run_in_bg"] = cb_bg

        self.ssl_var = tk.BooleanVar(value=config_store.SSL_ENABLED)
        cb_ssl = ttk.Checkbutton(toolbar, text=_("HTTPS/WSS (self-signed TLS)"), variable=self.ssl_var, command=self._on_ssl_changed)
        cb_ssl.pack(side="left", padx=(6, 0))
        self._header_widgets["cb_ssl"] = cb_ssl

        self.img_label = ttk.Label(self.top)
        self.img_label.pack(padx=10, pady=10)

        self.url_label = ttk.Label(self.top, font=("Arial", 12))
        self.url_label.pack(padx=10, pady=(0, 6))

        self.tip_label = ttk.Label(self.top, font=("Arial", 10), foreground="#333", justify="center")
        self.tip_label.pack(padx=10, pady=(0, 6))

        self.conn_label = ttk.Label(self.top, font=("Arial", 11, "bold"), foreground="#888")
        self.conn_label.pack(padx=10, pady=(0, 4))
        # Populate immediately if count is available
        if self.get_connection_count:
            try:
                _n = self.get_connection_count()
                _color = "#2ecc71" if _n > 0 else "#888"
                self.conn_label.configure(
                    text=_("Connected devices: {n}").format(n=_n),
                    foreground=_color,
                )
            except Exception:
                pass

        log_frame = ttk.LabelFrame(self.top, text=_("Log"), padding=4)
        log_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        log_inner = ttk.Frame(log_frame)
        log_inner.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_inner, height=6, font=("Consolas", 9), wrap="word", state="disabled")
        log_sb = ttk.Scrollbar(log_inner, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_sb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_sb.pack(side="right", fill="y")
        ttk.Button(log_frame, text=_("Clear"), command=self._clear_log).pack(anchor="e", pady=(4, 0))

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

    def _on_run_in_bg_changed(self):
        """Save run-in-background setting when checkbox changes."""
        config_store.RUN_IN_BACKGROUND = self.run_in_bg_var.get()
        config_store.save_config()
        self._refresh_qr_and_text()

    def _on_ssl_changed(self):
        """Save SSL setting; warn user that a restart is required."""
        config_store.SSL_ENABLED = self.ssl_var.get()
        config_store.save_config()
        messagebox.showinfo(
            _("HTTPS/WSS TLS"),
            _("TLS setting saved. Please restart the application for the change to take effect.\n\nNote: the browser/phone will show a certificate warning for the self-signed cert — tap 'Advanced' → 'Proceed' to continue."),
        )

    def _on_access_mode_changed(self):
        """Handle access mode radio button switch."""
        mode = self.access_mode_var.get()

        # Show/hide LAN IP row
        if mode == ACCESS_MODE_LAN:
            self._lan_row.pack(fill="x", padx=10, pady=(2, 2), after=self._mode_row)
            self._reload_ip_list_and_select_current()
        else:
            self._lan_row.pack_forget()

        if mode == ACCESS_MODE_PUBLIC:
            if not self.ssh_tunnel:
                # No tunnel support at all — revert back to LAN
                self.access_mode_var.set(ACCESS_MODE_LAN)
                self._lan_row.pack(fill="x", padx=10, pady=(2, 2), after=self._mode_row)
                messagebox.showinfo(_("Tunnel"), _("SSH tunnel is not available in this build."))
                return
            # If not configured, open settings first
            if not config_store.SSH_TUNNEL_HOST or not config_store.SSH_TUNNEL_USER:
                self._show_tunnel_settings()
                # After dialog closes, check again
                if not config_store.SSH_TUNNEL_HOST or not config_store.SSH_TUNNEL_USER:
                    # User cancelled — fall back to LAN
                    self.access_mode_var.set(ACCESS_MODE_LAN)
                    self._lan_row.pack(fill="x", padx=10, pady=(2, 2), after=self._mode_row)
                    return
            # Start tunnel if not active
            if not self.ssh_tunnel.is_active():
                def get_tunnel_config():
                    return {
                        "host": config_store.SSH_TUNNEL_HOST,
                        "port": config_store.SSH_TUNNEL_PORT,
                        "username": config_store.SSH_TUNNEL_USER,
                        "password": config_store.SSH_TUNNEL_PASSWORD,
                        "key_path": config_store.SSH_TUNNEL_KEY_PATH,
                        "remote_port": config_store.SSH_REMOTE_PORT,
                    }
                err = self.ssh_tunnel.start(refresh_config=get_tunnel_config)
                if err:
                    self.log(f"[Tunnel] {err}")
                    messagebox.showerror(_("SSH tunnel error"), err)
                    # Revert to LAN
                    self.access_mode_var.set(ACCESS_MODE_LAN)
                    self._lan_row.pack(fill="x", padx=10, pady=(2, 2), after=self._mode_row)
                    return
        else:
            # Switched away from public — stop tunnel if running
            if self.ssh_tunnel and self.ssh_tunnel.is_active():
                self.ssh_tunnel.stop()

        self._refresh_qr_and_text()

    def _update_tunnel_button_text(self):
        """Sync radio button state with actual tunnel status."""
        if not self.ssh_tunnel:
            return
        if self.ssh_tunnel.is_active():
            self.access_mode_var.set(ACCESS_MODE_PUBLIC)
            if hasattr(self, "_lan_row") and self._lan_row:
                self._lan_row.pack_forget()
        else:
            if self.access_mode_var.get() == ACCESS_MODE_PUBLIC:
                self.access_mode_var.set(ACCESS_MODE_LAN)
                if hasattr(self, "_lan_row") and self._lan_row and hasattr(self, "_mode_row") and self._mode_row:
                    self._lan_row.pack(fill="x", padx=10, pady=(2, 2), after=self._mode_row)

    def _on_tunnel_toggle(self):
        """Legacy: toggle tunnel via radio mode change."""
        if not self.ssh_tunnel:
            return
        if self.ssh_tunnel.is_active():
            self.access_mode_var.set(ACCESS_MODE_LAN)
        else:
            self.access_mode_var.set(ACCESS_MODE_PUBLIC)
        self._on_access_mode_changed()

    def _on_kill_remote_port(self):
        """Connect via SSH and kill the process occupying the remote port, with safety checks."""
        if not self.ssh_tunnel:
            return

        host = config_store.SSH_TUNNEL_HOST
        port = config_store.SSH_TUNNEL_PORT
        user = config_store.SSH_TUNNEL_USER
        key_path = config_store.SSH_TUNNEL_KEY_PATH
        remote_port = config_store.SSH_REMOTE_PORT

        if not host or not user:
            messagebox.showerror(
                _("SSH tunnel error"),
                _("SSH server and username are required. Please configure the tunnel first."),
            )
            return

        self.log(_("Checking port {port} on {host}...").format(port=remote_port, host=host))
        if hasattr(self, "btn_kill_port") and self.btn_kill_port:
            try:
                self.btn_kill_port.configure(state="disabled")
            except Exception:
                pass

        def _do_check():
            import shutil
            import subprocess

            result = {"proc_name": None, "pid": None, "error": None, "killed": False}

            ssh_exe = shutil.which("ssh")
            if not ssh_exe and os.name == "nt":
                for p in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")):
                    candidate = os.path.join(p or "", "OpenSSH", "ssh.exe")
                    if os.path.isfile(candidate):
                        ssh_exe = candidate
                        break

            if not ssh_exe:
                result["error"] = _("System ssh not found. Cannot check remote port.")
                return result

            base_cmd = [ssh_exe, "-o", "StrictHostKeyChecking=no",
                        "-o", "ConnectTimeout=10", "-o", "AddressFamily=inet"]
            if key_path:
                base_cmd.extend(["-i", key_path])
            if port != 22:
                base_cmd.extend(["-p", str(port)])
            target = f"{user}@{host}"

            # Query: find what program holds the port (ss is fastest; fallback lsof)
            query = (
                f"ss -tlnp 'sport = :{remote_port}' 2>/dev/null | "
                f"grep -oP 'pid=\\K[0-9]+' | head -1 | "
                f"xargs -I{{}} sh -c 'cat /proc/{{}}/comm 2>/dev/null || ps -p {{}} -o comm= 2>/dev/null'"
            )
            pid_query = (
                f"ss -tlnp 'sport = :{remote_port}' 2>/dev/null | "
                f"grep -oP 'pid=\\K[0-9]+' | head -1"
            )

            try:
                r = subprocess.run(
                    base_cmd + [target, query],
                    capture_output=True, text=True, timeout=15,
                    encoding="utf-8", errors="replace",
                )
                proc_name = (r.stdout or "").strip().lower()
                if not proc_name:
                    # fallback: try lsof
                    query2 = (
                        f"lsof -ti tcp:{remote_port} 2>/dev/null | head -1 | "
                        f"xargs -I{{}} sh -c 'cat /proc/{{}}/comm 2>/dev/null || ps -p {{}} -o comm= 2>/dev/null'"
                    )
                    r2 = subprocess.run(
                        base_cmd + [target, query2],
                        capture_output=True, text=True, timeout=15,
                        encoding="utf-8", errors="replace",
                    )
                    proc_name = (r2.stdout or "").strip().lower()

                result["proc_name"] = proc_name if proc_name else None

                # Also grab PID for display
                r_pid = subprocess.run(
                    base_cmd + [target, pid_query],
                    capture_output=True, text=True, timeout=15,
                    encoding="utf-8", errors="replace",
                )
                result["pid"] = (r_pid.stdout or "").strip() or None

            except subprocess.TimeoutExpired:
                result["error"] = _("SSH connection timed out while checking remote port.")
                return result
            except Exception as e:
                result["error"] = str(e)
                return result

            if not result["proc_name"]:
                # Port not occupied — nothing to do
                result["error"] = _("Port {port} on {host} does not appear to be occupied.").format(
                    port=remote_port, host=host
                )
                return result

            # Safety gate: only auto-kill sshd
            if "sshd" not in result["proc_name"]:
                # Return without killing — UI will show warning
                return result

            # It IS sshd — kill it
            kill_cmd = (
                f"fuser -k {remote_port}/tcp 2>/dev/null; "
                f"pid=$(lsof -ti tcp:{remote_port} 2>/dev/null); "
                f"[ -n \"$pid\" ] && kill $pid 2>/dev/null; "
                f"sleep 1"
            )
            try:
                subprocess.run(
                    base_cmd + [target, kill_cmd],
                    capture_output=True, text=True, timeout=18,
                    encoding="utf-8", errors="replace",
                )
                result["killed"] = True
            except subprocess.TimeoutExpired:
                result["error"] = _("Timeout while killing remote process.")
            except Exception as e:
                result["error"] = str(e)

            return result

        def _thread_fn():
            res = _do_check()

            def _on_done():
                if hasattr(self, "btn_kill_port") and self.btn_kill_port:
                    try:
                        self.btn_kill_port.configure(state="normal")
                    except Exception:
                        pass

                if res.get("error"):
                    self.log(f"[Kill port] {res['error']}")
                    messagebox.showwarning(_("Kill remote port"), res["error"])
                    return

                proc = res.get("proc_name") or "unknown"
                pid = res.get("pid") or "?"

                if "sshd" not in proc:
                    msg = _(
                        "Port {port} on {host} is occupied by '{proc}' (PID {pid}), "
                        "which is NOT sshd.\n\n"
                        "This program will NOT be killed automatically.\n\n"
                        "Please consider changing the remote port in Tunnel config to a different one."
                    ).format(port=remote_port, host=host, proc=proc, pid=pid)
                    self.log(f"[Kill port] Port {remote_port} held by '{proc}' (PID {pid}) — not killed.")
                    messagebox.showwarning(_("Kill remote port"), msg)
                    return

                if res.get("killed"):
                    self.log(f"[Kill port] sshd process (PID {pid}) on port {remote_port} killed successfully.")
                    messagebox.showinfo(
                        _("Kill remote port"),
                        _("sshd process (PID {pid}) holding port {port} has been killed.\n"
                          "You can now retry the SSH tunnel connection.").format(
                            pid=pid, port=remote_port
                        ),
                    )
                else:
                    self.log(f"[Kill port] Failed to kill sshd (PID {pid}): {res.get('error', '')}")
                    messagebox.showerror(
                        _("Kill remote port"),
                        _("Failed to kill sshd process: {err}").format(err=res.get("error", "")),
                    )

            self.cmd_q.put(("call", _on_done))

        threading.Thread(target=_thread_fn, daemon=True).start()

    def _show_tunnel_settings(self):
        """Open SSH tunnel settings dialog."""
        dlg = tk.Toplevel(self.top if self.top else self.root)
        dlg.title(_("Public exposure (SSH tunnel)"))
        dlg.transient(self.top if self.top else self.root)
        dlg.grab_set()
        dlg.resizable(False, False)

        f = ttk.Frame(dlg, padding=12)
        f.pack(fill="both", expand=True)

        ttk.Label(f, text=_("SSH server (host or IP):")).pack(anchor="w")
        entry_host = ttk.Entry(f, width=42)
        entry_host.insert(0, config_store.SSH_TUNNEL_HOST or "")
        entry_host.pack(fill="x", pady=(2, 8))

        ttk.Label(f, text=_("SSH port:")).pack(anchor="w")
        entry_port = ttk.Entry(f, width=10)
        entry_port.insert(0, str(config_store.SSH_TUNNEL_PORT or 22))
        entry_port.pack(fill="x", pady=(2, 8))

        ttk.Label(f, text=_("SSH username:")).pack(anchor="w")
        entry_user = ttk.Entry(f, width=42)
        entry_user.insert(0, config_store.SSH_TUNNEL_USER or "")
        entry_user.pack(fill="x", pady=(2, 8))

        ttk.Label(f, text=_("SSH password (optional, prefer key):")).pack(anchor="w")
        entry_pass = ttk.Entry(f, width=42, show="*")
        entry_pass.insert(0, config_store.SSH_TUNNEL_PASSWORD or "")
        entry_pass.pack(fill="x", pady=(2, 8))

        ttk.Label(f, text=_("Private key path (optional):")).pack(anchor="w")
        entry_key = ttk.Entry(f, width=42)
        entry_key.insert(0, config_store.SSH_TUNNEL_KEY_PATH or "")
        entry_key.pack(fill="x", pady=(2, 8))

        ttk.Label(f, text=_("Remote port on server:")).pack(anchor="w")
        entry_remote = ttk.Entry(f, width=10)
        entry_remote.insert(0, str(config_store.SSH_REMOTE_PORT or 8080))
        entry_remote.pack(fill="x", pady=(2, 12))

        tip = ttk.Label(f, text=_("Server needs GatewayPorts yes in sshd_config for public access."), font=("Arial", 9), foreground="#666")
        tip.pack(anchor="w", pady=(0, 4))
        tip2 = ttk.Label(f, text=_("Use key auth (recommended) for WebSocket reliability."), font=("Arial", 9), foreground="#666")
        tip2.pack(anchor="w", pady=(0, 8))

        def on_ok():
            host = (entry_host.get() or "").strip()
            try:
                port = int((entry_port.get() or "22").strip())
            except ValueError:
                port = 22
            user = (entry_user.get() or "").strip()
            password = (entry_pass.get() or "").strip() or None
            key_path = (entry_key.get() or "").strip() or None
            try:
                remote_port = int((entry_remote.get() or "8080").strip())
            except ValueError:
                remote_port = 8080
            config_store.SSH_TUNNEL_HOST = host or None
            config_store.SSH_TUNNEL_PORT = port
            config_store.SSH_TUNNEL_USER = user or None
            config_store.SSH_TUNNEL_PASSWORD = password
            config_store.SSH_TUNNEL_KEY_PATH = key_path
            config_store.SSH_REMOTE_PORT = remote_port
            config_store.save_config()
            dlg.destroy()

        def on_cancel():
            dlg.destroy()

        btn_f = ttk.Frame(f)
        btn_f.pack(fill="x", pady=(4, 0))
        ttk.Button(btn_f, text=_("OK"), command=on_ok).pack(side="left", padx=(0, 6))
        ttk.Button(btn_f, text=_("Cancel"), command=on_cancel).pack(side="left")

        dlg.update_idletasks()
        dlg.geometry(f"+{dlg.winfo_screenwidth()//2 - dlg.winfo_reqwidth()//2}+{dlg.winfo_screenheight()//2 - dlg.winfo_reqheight()//2}")

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
        if "btn_tunnel_cfg" in self._header_widgets:
            self._header_widgets["btn_tunnel_cfg"].configure(text=_("Tunnel config"))
        if "btn_kill_port" in self._header_widgets:
            self._header_widgets["btn_kill_port"].configure(text=_("Kill remote port"))
        self._header_widgets["cb_run_in_bg"].configure(text=_("Run in background when closed"))
        if "cb_ssl" in self._header_widgets:
            self._header_widgets["cb_ssl"].configure(text=_("HTTPS/WSS (self-signed TLS)"))
        if self.ssl_var:
            self.ssl_var.set(config_store.SSL_ENABLED)
        # Refresh radio button labels
        for mode_value, label_key in (
            (ACCESS_MODE_LOCAL, "Local (this machine only)"),
            (ACCESS_MODE_LAN,   "LAN (same network)"),
            (ACCESS_MODE_PUBLIC, "Public (internet)"),
        ):
            if mode_value in self._access_radio_btns:
                self._access_radio_btns[mode_value].configure(text=_(label_key))
        if self.run_in_bg_var:
            self.run_in_bg_var.set(config_store.RUN_IN_BACKGROUND)
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
            dlg.destroy()

        def on_cancel():
            dlg.destroy()

        btn_f = ttk.Frame(f)
        btn_f.pack(fill="x", pady=(4, 0))
        ttk.Button(btn_f, text=_("OK"), command=on_ok).pack(side="left", padx=(0, 6))
        ttk.Button(btn_f, text=_("Cancel"), command=on_cancel).pack(side="left")

        dlg.update_idletasks()
        dlg.geometry(f"+{dlg.winfo_screenwidth()//2 - dlg.winfo_reqwidth()//2}+{dlg.winfo_screenheight()//2 - dlg.winfo_reqheight()//2}")

    def _is_public_connecting(self) -> bool:
        """Return True when in public mode but tunnel is not yet active."""
        mode = self.access_mode_var.get() if hasattr(self, "access_mode_var") else ACCESS_MODE_LAN
        if mode != ACCESS_MODE_PUBLIC:
            return False
        return not (self.ssh_tunnel and self.ssh_tunnel.is_active())

    def _get_url_for_mode(self) -> str:
        """Return URL appropriate for the current access mode."""
        mode = self.access_mode_var.get() if hasattr(self, "access_mode_var") else ACCESS_MODE_LAN
        if mode == ACCESS_MODE_PUBLIC:
            if self.ssh_tunnel and self.ssh_tunnel.is_active():
                return self.get_payload_url() or ""
            # Tunnel not active yet — return empty to show loading
            return ""
        if mode == ACCESS_MODE_LOCAL:
            return self._build_url_for_ip("127.0.0.1")
        # LAN: use the selected NIC IP
        return self._build_url_for_ip(self._selected_ip())

    def _build_url_for_ip(self, ip: str) -> str:
        """Build a URL for a specific IP using the injected callback, or fall back."""
        if self.build_url_for_ip:
            return self.build_url_for_ip(ip) or ""
        return self.get_payload_url() or ""

    def _refresh_qr_and_text(self):
        mode = self.access_mode_var.get() if hasattr(self, "access_mode_var") else ACCESS_MODE_LAN
        connecting = self._is_public_connecting()

        if connecting:
            # Public mode, tunnel not yet active — show loading placeholder
            self._show_loading_qr()
            self.url_label.configure(text="")
            self.tip_label.configure(
                text=_("Connecting to SSH tunnel, please wait…")
            )
            self._start_loading_animation()
            return

        # Stop any running loading animation once tunnel is up / mode changed
        self._stop_loading_animation()

        url = self._get_url_for_mode()
        if not url:
            return

        qr = qrcode.QRCode(box_size=8, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        self.tk_img = ImageTk.PhotoImage(img)

        self.img_label.configure(image=self.tk_img, text="", compound="none")
        self.url_label.configure(text=url)

        if mode == ACCESS_MODE_LOCAL:
            ip_show = "127.0.0.1"
        elif mode == ACCESS_MODE_PUBLIC:
            ip_show = self.get_effective_ip()
        else:
            ip_show = self._selected_ip()
        ip_mode = _("Manual") if (self.get_user_ip() and self.get_user_ip().strip()) else _("Auto")
        http_port, ws_port = self.get_ports()
        scheme = "HTTPS" if config_store.SSL_ENABLED else "HTTP"
        ws_scheme = "WSS" if config_store.SSL_ENABLED else "WS"
        llm_line = _("LLM: {model} (enabled)").format(model=config_store.LLM_MODEL) if config_store.LLM_ENABLED else _("LLM: disabled")
        if self.dev_mode:
            close_tip = _("Closing this window will exit the app")
        elif config_store.RUN_IN_BACKGROUND:
            close_tip = _("Closing this window does not affect background running")
        else:
            close_tip = _("Closing this window will exit the app")
        if mode == ACCESS_MODE_PUBLIC:
            scan_tip = _("Scan with phone (public internet, anywhere)")
        elif mode == ACCESS_MODE_LOCAL:
            scan_tip = _("Local access (this machine only)")
        else:
            scan_tip = _("Scan with phone to open page (same WiFi / same subnet)")
        self.tip_label.configure(
            text=scan_tip + "\n"
            + _("Mode: {mode}  IP: {ip}").format(mode=ip_mode, ip=ip_show) + "\n"
            + f"{scheme}:{http_port}  {ws_scheme}:{http_port}" + "\n"
            + llm_line + "\n"
            + close_tip + "\n"
            + _("Config file: {path}").format(path=self.get_config_path())
        )

    def _show_loading_qr(self):
        """Replace QR image area with a grey loading placeholder."""
        size = 240
        img = Image.new("RGB", (size, size), color="#e8e8e8")
        self.tk_img = ImageTk.PhotoImage(img)
        self.img_label.configure(image=self.tk_img, text="", compound="none")

    # ── Loading animation helpers ─────────────────────────────────────────────

    def _start_loading_animation(self):
        """Start (or restart) a simple dots animation on tip_label."""
        if getattr(self, "_loading_after_id", None) is not None:
            return  # already running
        self._loading_dot_count = 0
        self._loading_animate()

    def _stop_loading_animation(self):
        """Cancel pending loading animation callback."""
        after_id = getattr(self, "_loading_after_id", None)
        if after_id is not None:
            try:
                self.root.after_cancel(after_id)
            except Exception:
                pass
            self._loading_after_id = None

    def _loading_animate(self):
        """Tick the dots animation; re-schedules itself until stopped."""
        if not self._is_public_connecting():
            self._loading_after_id = None
            return
        dots = "." * ((self._loading_dot_count % 3) + 1)
        self._loading_dot_count += 1
        if self.top and self.tip_label:
            try:
                self.tip_label.configure(
                    text=_("Connecting to SSH tunnel, please wait") + dots
                )
            except Exception:
                pass
        self._loading_after_id = self.root.after(500, self._loading_animate)

    def _show_window(self):
        self._ensure_window()

        try:
            self.top.deiconify()
            self.top.lift()
            self.top.attributes("-topmost", True)
            self.top.after(200, lambda: self.top.attributes("-topmost", False))
        except Exception:
            pass

        # Sync LAN row visibility with current mode
        if self.access_mode_var.get() == ACCESS_MODE_LAN:
            self._lan_row.pack(fill="x", padx=10, pady=(2, 2), after=self._mode_row)
        else:
            self._lan_row.pack_forget()

        self._reload_ip_list_and_select_current()
        self._refresh_qr_and_text()
        self.log(_("Log area: tunnel status and errors will appear here"))
