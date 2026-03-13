"""Self-signed TLS certificate generation for local HTTPS/WSS.

Uses the `cryptography` package when available; falls back to `openssl` CLI.
Certificate and key are stored beside the exe (or in user home as fallback).
The cert is valid for 10 years and covers common LAN/localhost SANs.
"""
from __future__ import annotations

import datetime
import ipaddress
import os
import subprocess
from typing import Optional, Tuple

from paths import get_exe_dir

_CERT_FILENAME = "lanvi_cert.pem"
_KEY_FILENAME = "lanvi_key.pem"

_CERT_PRIMARY = os.path.join(get_exe_dir(), _CERT_FILENAME)
_KEY_PRIMARY = os.path.join(get_exe_dir(), _KEY_FILENAME)
_CERT_FALLBACK = os.path.join(os.path.expanduser("~"), _CERT_FILENAME)
_KEY_FALLBACK = os.path.join(os.path.expanduser("~"), _KEY_FILENAME)


def _paths_exist(cert: str, key: str) -> bool:
    return os.path.isfile(cert) and os.path.isfile(key)


def get_cert_paths() -> Tuple[Optional[str], Optional[str]]:
    """Return (cert_path, key_path) of existing certificate, or (None, None)."""
    if _paths_exist(_CERT_PRIMARY, _KEY_PRIMARY):
        return _CERT_PRIMARY, _KEY_PRIMARY
    if _paths_exist(_CERT_FALLBACK, _KEY_FALLBACK):
        return _CERT_FALLBACK, _KEY_FALLBACK
    return None, None


def _try_write(cert_pem: bytes, key_pem: bytes, cert_path: str, key_path: str) -> bool:
    try:
        with open(cert_path, "wb") as f:
            f.write(cert_pem)
        with open(key_path, "wb") as f:
            f.write(key_pem)
        return True
    except Exception:
        return False


def _generate_with_cryptography(cert_path: str, key_path: str) -> bool:
    """Generate using the `cryptography` library (preferred)."""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        return False

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "LanVI Self-Signed"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "LanVoiceInput"),
    ])

    san = x509.SubjectAlternativeName([
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        # Wildcard for LAN addresses can't be an IP SAN; we include a broad DNS entry
        x509.DNSName("*.local"),
    ])

    now = datetime.datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(san, critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )

    return _try_write(cert_pem, key_pem, cert_path, key_path)


def _generate_with_openssl(cert_path: str, key_path: str) -> bool:
    """Fallback: generate via `openssl` CLI."""
    try:
        subprocess.check_call(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-keyout", key_path,
                "-out", cert_path,
                "-days", "3650",
                "-nodes",
                "-subj", "/CN=LanVI Self-Signed/O=LanVoiceInput",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        return _paths_exist(cert_path, key_path)
    except Exception:
        return False


def ensure_cert() -> Tuple[Optional[str], Optional[str]]:
    """Return (cert_path, key_path), generating a new cert if needed.

    Returns (None, None) on failure.
    """
    cert, key = get_cert_paths()
    if cert and key:
        return cert, key

    # Try primary location first, fall back to user home
    for cert_path, key_path in [(_CERT_PRIMARY, _KEY_PRIMARY), (_CERT_FALLBACK, _KEY_FALLBACK)]:
        if _generate_with_cryptography(cert_path, key_path):
            return cert_path, key_path
        if _generate_with_openssl(cert_path, key_path):
            return cert_path, key_path

    return None, None


def delete_cert() -> None:
    """Remove existing certificate files so they will be regenerated on next call."""
    for path in (_CERT_PRIMARY, _KEY_PRIMARY, _CERT_FALLBACK, _KEY_FALLBACK):
        try:
            if os.path.isfile(path):
                os.remove(path)
        except Exception:
            pass
