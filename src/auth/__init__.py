"""OAuth connections for mail, calendar, and other personal accounts."""

from src.auth.oauth import (
    OAuthError,
    connect_google,
    disconnect_google,
    missing_client_id_message,
)
from src.auth.store import AuthStore, auth_status_line, default_auth_path

__all__ = [
    "AuthStore",
    "OAuthError",
    "auth_status_line",
    "connect_google",
    "default_auth_path",
    "disconnect_google",
    "missing_client_id_message",
]
