# -*- coding: utf-8 -*-
"""
SSH reverse tunnel for exposing local HTTP server to public internet.
Prefers system ssh (more reliable for WebSocket); falls back to paramiko when password auth.
"""
import os
import shutil
import socket
import subprocess
import threading
from typing import Callable, Optional

from i18n import _

_paramiko = None


def _get_paramiko():
    global _paramiko
    if _paramiko is None:
        try:
            import paramiko
            _paramiko = paramiko
        except ImportError:
            raise ImportError("paramiko is required for SSH tunnel. Run: pip install paramiko")
    return _paramiko


def _pipe(src, dst):
    """Bidirectional pipe between two socket-like objects."""
    try:
        while True:
            data = src.recv(4096)
            if not data:
                break
            dst.sendall(data)
    except Exception:
        pass
    finally:
        try:
            if hasattr(dst, "shutdown"):
                dst.shutdown(socket.SHUT_WR)
        except Exception:
            pass


def _find_ssh() -> Optional[str]:
    """Find system ssh executable."""
    ssh = shutil.which("ssh")
    if ssh:
        return ssh
    if os.name == "nt":
        for p in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")):
            exe = os.path.join(p or "", "OpenSSH", "ssh.exe")
            if os.path.isfile(exe):
                return exe
    return None


class SSHTunnelManager:
    """
    Manages SSH reverse tunnel: server:remote_port -> localhost:local_port.
    Same as: ssh -R remote_port:localhost:local_port user@host
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        local_port: int,
        remote_port: Optional[int] = None,
        password: Optional[str] = None,
        key_path: Optional[str] = None,
        on_state_change: Optional[Callable[[bool, Optional[str]], None]] = None,
    ):
        self.host = host.strip()
        self.port = port
        self.username = username.strip()
        self.local_port = local_port
        self.remote_port = remote_port or local_port
        self.password = password
        self.key_path = (key_path or "").strip() or None
        self.on_state_change = on_state_change

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._active = False
        self._error: Optional[str] = None
        self._client = None
        self._transport = None
        self._proc: Optional[subprocess.Popen] = None  # for system ssh

    def is_active(self) -> bool:
        return self._active

    def get_public_url(self, token: Optional[str] = None, locale: Optional[str] = None) -> str:
        """Build public URL for QR code: http://host:remote_port?token=...&lang=..."""
        base = f"http://{self.host}:{self.remote_port}"
        params = []
        if token and token.strip():
            params.append(f"token={token.strip()}")
        if locale and locale.strip():
            params.append(f"lang={locale.strip()}")
        if params:
            return f"{base}?{'&'.join(params)}"
        return base

    def _notify(self, active: bool, error: Optional[str] = None):
        self._active = active
        self._error = error
        if self.on_state_change:
            try:
                self.on_state_change(active, error)
            except Exception:
                pass

    def start(self, refresh_config=None) -> Optional[str]:
        """
        Start tunnel in background. Returns None on success, error message on failure.
        If refresh_config() is provided and returns (host, port, user, ...), use those values.
        """
        if self._active or (self._thread and self._thread.is_alive()):
            return _("Tunnel already running")

        if refresh_config:
            try:
                cfg = refresh_config()
                if cfg:
                    self.host = (cfg.get("host") or "").strip()
                    self.port = int(cfg.get("port") or 22)
                    self.username = (cfg.get("username") or "").strip()
                    self.password = (cfg.get("password") or "").strip() or None
                    self.key_path = (cfg.get("key_path") or "").strip() or None
                    self.remote_port = int(cfg.get("remote_port") or self.local_port)
            except Exception:
                pass

        if not self.host or not self.username:
            return _("SSH host and username are required")

        self._stop_event.clear()
        use_system_ssh = bool(self.key_path) and _find_ssh()
        if use_system_ssh:
            self._thread = threading.Thread(target=self._run_system_ssh, daemon=True)
        else:
            self._thread = threading.Thread(target=self._run_tunnel, daemon=True)
        self._thread.start()
        return None

    def _run_system_ssh(self):
        """Use system ssh -R for tunnel (more reliable for WebSocket). Auto-restarts on drop."""
        SSH_RETRY_DELAYS = [3, 5, 10, 20, 30]  # seconds between reconnect attempts
        retry_count = 0

        while not self._stop_event.is_set():
            try:
                ssh_exe = _find_ssh()
                if not ssh_exe:
                    raise RuntimeError(_("System ssh not found"))
                key_opt = ["-i", self.key_path] if self.key_path else []
                cmd = [
                    ssh_exe,
                    "-R", f"{self.remote_port}:127.0.0.1:{self.local_port}",
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "ServerAliveInterval=20",
                    "-o", "ServerAliveCountMax=3",
                    "-o", "ExitOnForwardFailure=yes",
                    "-o", "AddressFamily=inet",
                    "-o", "TCPKeepAlive=yes",
                    "-N",
                    *key_opt,
                ]
                if self.port != 22:
                    cmd.extend(["-p", str(self.port)])
                cmd.append(f"{self.username}@{self.host}")
                self._proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                # Wait briefly to see if ssh exits immediately (e.g. port forward rejected)
                try:
                    self._proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    # Process still running after 2s -> tunnel established
                    self._notify(True, None)
                    retry_count = 0
                    self._proc.wait()  # Block until the tunnel drops
                    # Tunnel dropped — loop will reconnect unless stopped
                    if not self._stop_event.is_set():
                        err = (self._proc.stderr.read() or "").strip() if self._proc.stderr else ""
                        self._notify(False, err or _("Tunnel dropped, reconnecting…"))
                    continue

                # Process exited immediately - read stderr for error
                err = ""
                if self._proc and self._proc.stderr:
                    err = (self._proc.stderr.read() or "").strip()
                if self._stop_event.is_set():
                    break
                self._notify(False, err or _("SSH exited immediately"))

            except Exception as e:
                if self._stop_event.is_set():
                    break
                self._notify(False, str(e))
            finally:
                if self._proc:
                    try:
                        self._proc.terminate()
                        self._proc.wait(timeout=3)
                    except Exception:
                        try:
                            self._proc.kill()
                        except Exception:
                            pass
                    self._proc = None

            # Wait before retry
            if self._stop_event.is_set():
                break
            delay = SSH_RETRY_DELAYS[min(retry_count, len(SSH_RETRY_DELAYS) - 1)]
            retry_count += 1
            print(f"[tunnel] reconnecting in {delay}s (attempt {retry_count})…")
            self._stop_event.wait(timeout=delay)

        self._notify(False, None)

    def _run_tunnel(self):
        """Background thread: connect, request reverse forward, accept and pipe. Auto-restarts on drop."""
        SSH_RETRY_DELAYS = [3, 5, 10, 20, 30]
        retry_count = 0

        while not self._stop_event.is_set():
            try:
                paramiko = _get_paramiko()
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

                connect_kw = {
                    "hostname": self.host,
                    "port": self.port,
                    "username": self.username,
                }
                if self.key_path:
                    connect_kw["key_filename"] = self.key_path
                if self.password:
                    connect_kw["password"] = self.password

                client.connect(**connect_kw)
                self._client = client
                self._transport = client.get_transport()
                if not self._transport:
                    raise RuntimeError(_("SSH connection failed"))

                # Request server to listen on remote_port; forward to localhost:local_port
                self._transport.request_port_forward("", self.remote_port)
                self._notify(True, None)
                retry_count = 0

                while not self._stop_event.is_set() and self._transport.is_active():
                    chan = self._transport.accept(1.0)
                    if chan is None:
                        continue
                    thr = threading.Thread(
                        target=self._handler,
                        args=(chan, "127.0.0.1", self.local_port),
                        daemon=True,
                    )
                    thr.start()

                if not self._stop_event.is_set():
                    self._notify(False, _("Tunnel dropped, reconnecting…"))

            except Exception as e:
                if self._stop_event.is_set():
                    break
                msg = str(e)
                if "Authentication" in msg or "auth" in msg.lower():
                    # Auth errors won't resolve by retrying — give up
                    self._notify(False, _("SSH authentication failed: {e}").format(e=msg))
                    break
                elif "connect" in msg.lower() or "timeout" in msg.lower():
                    self._notify(False, _("Cannot connect to SSH server: {e}").format(e=msg))
                else:
                    self._notify(False, _("SSH error: {e}").format(e=msg))
            finally:
                try:
                    if self._transport:
                        self._transport.cancel_port_forward("", self.remote_port)
                except Exception:
                    pass
                try:
                    if self._client:
                        self._client.close()
                except Exception:
                    pass
                self._client = None
                self._transport = None

            # Wait before retry
            if self._stop_event.is_set():
                break
            delay = SSH_RETRY_DELAYS[min(retry_count, len(SSH_RETRY_DELAYS) - 1)]
            retry_count += 1
            print(f"[tunnel] reconnecting in {delay}s (attempt {retry_count})…")
            self._stop_event.wait(timeout=delay)

        self._notify(False, None)

    def _handler(self, chan, host, port):
        """Forward SSH channel to local socket (from paramiko rforward demo)."""
        sock = socket.socket()
        try:
            sock.settimeout(10)
            sock.connect((host, port))
        except Exception:
            chan.close()
            return
        try:
            thr1 = threading.Thread(target=_pipe, args=(chan, sock), daemon=True)
            thr2 = threading.Thread(target=_pipe, args=(sock, chan), daemon=True)
            thr1.start()
            thr2.start()
            thr1.join()
            thr2.join()
        finally:
            try:
                chan.close()
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass

    def stop(self):
        """Stop the tunnel."""
        self._stop_event.set()
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
        try:
            if self._transport:
                self._transport.close()
        except Exception:
            pass
        self._transport = None
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        self._notify(False, None)
