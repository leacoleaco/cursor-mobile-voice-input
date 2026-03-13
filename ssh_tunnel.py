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

    def get_public_url(self, token: Optional[str] = None, locale: Optional[str] = None, ssl: bool = False) -> str:
        """Build public URL for QR code: http(s)://host:remote_port?token=...&lang=..."""
        scheme = "https" if ssl else "http"
        base = f"{scheme}://{self.host}:{self.remote_port}"
        params = []
        if token and token.strip():
            params.append(f"token={token.strip()}")
        if locale and locale.strip():
            params.append(f"lang={locale.strip()}")
        if params:
            return f"{base}?{'&'.join(params)}"
        return base

    def _notify(self, active: bool, error: Optional[str] = None):
        # Suppress redundant inactive notifications to avoid spamming the UI on every retry.
        # Always fire when transitioning active<->inactive, or when there's a new error message.
        prev_active = self._active
        prev_error = self._error
        self._active = active
        self._error = error
        state_changed = (active != prev_active) or (active and error != prev_error)
        error_changed = bool(error) and error != prev_error
        if not state_changed and not error_changed:
            return
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

    def _kill_remote_port(self, ssh_exe: str, key_opt: list) -> None:
        """Try to kill any process on the server that holds self.remote_port, so the next
        -R forward succeeds immediately instead of failing with 'port forwarding failed'."""
        cmd = [
            ssh_exe,
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=8",
            "-o", "AddressFamily=inet",
            *key_opt,
        ]
        if self.port != 22:
            cmd.extend(["-p", str(self.port)])
        cmd.append(f"{self.username}@{self.host}")
        # fuser kills the process holding the port; fallback to lsof+kill for macOS/BSD
        kill_cmd = (
            f"fuser -k {self.remote_port}/tcp 2>/dev/null; "
            f"pid=$(lsof -ti tcp:{self.remote_port} 2>/dev/null); "
            f"[ -n \"$pid\" ] && kill $pid 2>/dev/null; "
            f"sleep 1"
        )
        cmd.append(kill_cmd)
        try:
            subprocess.run(cmd, timeout=12, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    def _run_system_ssh(self):
        """Use system ssh -R for tunnel (more reliable for WebSocket). Auto-restarts on drop."""
        # Normal retry delays; port-busy errors use longer delays to let the server release the port.
        SSH_RETRY_DELAYS = [5, 10, 15, 20, 30]
        SSH_PORT_BUSY_DELAYS = [15, 20, 30, 45, 60]
        retry_count = 0
        port_busy_count = 0

        while not self._stop_event.is_set():
            err = ""
            try:
                ssh_exe = _find_ssh()
                if not ssh_exe:
                    raise RuntimeError(_("System ssh not found"))
                key_opt = ["-i", self.key_path] if self.key_path else []
                cmd = [
                    ssh_exe,
                    # Bind on all interfaces so the port is reachable from outside
                    "-R", f"0.0.0.0:{self.remote_port}:127.0.0.1:{self.local_port}",
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
                # Give ssh up to 8s to either establish the tunnel or fail fast
                try:
                    self._proc.wait(timeout=8.0)
                except subprocess.TimeoutExpired:
                    # Still running after 8s -> tunnel is up
                    self._notify(True, None)
                    retry_count = 0
                    port_busy_count = 0
                    self._proc.wait()  # Block until the tunnel drops naturally
                    if not self._stop_event.is_set():
                        try:
                            err = (self._proc.stderr.read() or "").strip()
                        except Exception:
                            err = ""
                        self._notify(False, err or _("Tunnel dropped, reconnecting..."))
                    continue

                # Process exited within 8s -- read stderr to classify the error
                try:
                    err = (self._proc.stderr.read() or "").strip()
                except Exception:
                    err = ""
                if self._stop_event.is_set():
                    break
                self._notify(False, err or _("SSH exited immediately"))

            except Exception as e:
                if self._stop_event.is_set():
                    break
                err = str(e)
                self._notify(False, err)
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

            if self._stop_event.is_set():
                break

            # Detect "remote port forwarding failed" -- the server-side port is still held by a
            # previous ssh process. Use longer back-off to give the server time to release it.
            port_busy = "port forwarding failed" in err.lower() or "remote port" in err.lower()
            if port_busy:
                port_busy_count += 1
                delay = SSH_PORT_BUSY_DELAYS[min(port_busy_count - 1, len(SSH_PORT_BUSY_DELAYS) - 1)]
                print(f"[tunnel] remote port {self.remote_port} busy, waiting {delay}s for server to release...")
                # Actively kill the stale process on the server so next attempt succeeds sooner
                try:
                    ssh_exe2 = _find_ssh()
                    key_opt2 = ["-i", self.key_path] if self.key_path else []
                    if ssh_exe2:
                        self._kill_remote_port(ssh_exe2, key_opt2)
                except Exception:
                    pass
            else:
                port_busy_count = 0
                delay = SSH_RETRY_DELAYS[min(retry_count, len(SSH_RETRY_DELAYS) - 1)]
                retry_count += 1
                print(f"[tunnel] reconnecting in {delay}s (attempt {retry_count})...")

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
                    # Use a short timeout so new connections are picked up
                    # quickly (reduces per-connection latency vs the old 1.0s).
                    chan = self._transport.accept(0.05)
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
