"""Access token for HTTP and WebSocket. Required in header or query param."""
import secrets
from typing import Optional

# Generated at startup; used for both HTTP and WebSocket auth
_ACCESS_TOKEN: Optional[str] = None


def generate_token() -> str:
    """Generate a new random token (32 bytes hex = 64 chars)."""
    return secrets.token_hex(32)


def set_token(token: str):
    """Set the access token (called at server startup)."""
    global _ACCESS_TOKEN
    _ACCESS_TOKEN = token


def get_token() -> Optional[str]:
    """Get current access token."""
    return _ACCESS_TOKEN


def validate_request(
    auth_header: Optional[str],
    x_authority: Optional[str],
    x_token: Optional[str],
    query_token: Optional[str],
) -> bool:
    """
    Validate request has valid token.
    Accepts:
      - Authorization: Bearer <token>
      - X-Authority: <token>
      - X-Token: <token>
      - token query param
    """
    token = get_token()
    if not token:
        return True  # No token configured = no auth required (backward compat)

    provided = None
    if auth_header and auth_header.startswith("Bearer "):
        provided = auth_header[7:].strip()
    if not provided and x_authority:
        provided = x_authority.strip()
    if not provided and x_token:
        provided = x_token.strip()
    if not provided and query_token:
        provided = query_token.strip()

    return provided == token if provided else False
