"""Google OAuth (PKCE + localhost) for mail and calendar.

This is the "connect via auth" path: one AI key still runs Jarvis; Google
login is optional and uses a Desktop OAuth client ID, not a second AI vendor.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import threading
import webbrowser
from dataclasses import dataclass
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from src.auth.store import AuthAccount, AuthStore, _iso, _utc_now
from src.config import USER_AGENT

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

GOOGLE_SCOPES = (
    "openid",
    "email",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
)

TOKEN_TIMEOUT = 20
CALLBACK_TIMEOUT = 180


class OAuthError(RuntimeError):
    """User-facing OAuth failure (missing client, denied, timeout)."""


def google_client_id() -> str:
    return (os.getenv("GOOGLE_OAUTH_CLIENT_ID") or "").strip()


def google_client_secret() -> str:
    return (os.getenv("GOOGLE_OAUTH_CLIENT_SECRET") or "").strip()


def missing_client_id_message() -> str:
    return (
        "Google OAuth is not configured yet — this is not a second AI key.\n"
        "Create a Desktop OAuth client in Google Cloud Console, enable the\n"
        "Gmail API and Google Calendar API, then put the client id in .env:\n"
        "  GOOGLE_OAUTH_CLIENT_ID=....apps.googleusercontent.com\n"
        "Optional for web clients: GOOGLE_OAUTH_CLIENT_SECRET=...\n"
        "Then run: python main.py --connect google"
    )


def generate_pkce() -> tuple[str, str]:
    """Return (code_verifier, S256 code_challenge)."""
    verifier = _urlsafe(secrets.token_bytes(32))
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = _urlsafe(digest)
    return verifier, challenge


def new_state() -> str:
    return _urlsafe(secrets.token_bytes(16))


def build_authorization_url(
    redirect_uri: str,
    state: str,
    code_challenge: str,
    *,
    client_id: str | None = None,
    scopes: tuple[str, ...] = GOOGLE_SCOPES,
) -> str:
    cid = (client_id or google_client_id()).strip()
    if not cid:
        raise OAuthError(missing_client_id_message())
    query = urlencode(
        {
            "client_id": cid,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "include_granted_scopes": "true",
        }
    )
    return f"{GOOGLE_AUTH_URL}?{query}"


def parse_oauth_callback(path: str, expected_state: str) -> str:
    """Extract the authorization code from a localhost callback path."""
    parsed = urlparse(path)
    params = parse_qs(parsed.query)
    error = (params.get("error") or [""])[0].strip()
    if error:
        description = (params.get("error_description") or [""])[0].strip()
        detail = f"{error}: {description}" if description else error
        raise OAuthError(f"Google login was not completed ({detail}).")
    state = (params.get("state") or [""])[0]
    if state != expected_state:
        raise OAuthError("OAuth state mismatch — try connecting again.")
    code = (params.get("code") or [""])[0].strip()
    if not code:
        raise OAuthError("Google did not return an authorization code.")
    return code


@dataclass
class GoogleLoginSession:
    state: str
    verifier: str
    challenge: str
    redirect_uri: str
    auth_url: str
    client_id: str


def begin_google_login(
    redirect_uri: str,
    *,
    client_id: str | None = None,
) -> GoogleLoginSession:
    cid = (client_id or google_client_id()).strip()
    if not cid:
        raise OAuthError(missing_client_id_message())
    verifier, challenge = generate_pkce()
    state = new_state()
    auth_url = build_authorization_url(
        redirect_uri, state, challenge, client_id=cid
    )
    return GoogleLoginSession(
        state=state,
        verifier=verifier,
        challenge=challenge,
        redirect_uri=redirect_uri,
        auth_url=auth_url,
        client_id=cid,
    )


def exchange_code(
    login: GoogleLoginSession,
    code: str,
    *,
    http: requests.Session | None = None,
) -> dict:
    payload = {
        "client_id": login.client_id,
        "code": code,
        "code_verifier": login.verifier,
        "grant_type": "authorization_code",
        "redirect_uri": login.redirect_uri,
    }
    secret = google_client_secret()
    if secret:
        payload["client_secret"] = secret
    return _token_request(payload, http=http)


def refresh_google_account(
    account: AuthAccount,
    *,
    http: requests.Session | None = None,
    client_id: str | None = None,
) -> AuthAccount:
    if not account.refresh_token:
        raise OAuthError(
            "Google access expired and no refresh token is stored. "
            "Run: python main.py --connect google"
        )
    cid = (client_id or google_client_id() or "").strip()
    if not cid:
        raise OAuthError(missing_client_id_message())
    payload = {
        "client_id": cid,
        "grant_type": "refresh_token",
        "refresh_token": account.refresh_token,
    }
    secret = google_client_secret()
    if secret:
        payload["client_secret"] = secret
    data = _token_request(payload, http=http)
    return account_from_token_response(data, existing=account)


def account_from_token_response(
    data: dict,
    *,
    existing: AuthAccount | None = None,
    email: str = "",
) -> AuthAccount:
    now = _utc_now()
    expires_in = data.get("expires_in")
    expires_at = None
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        seconds = 0
    if seconds > 0:
        expires_at = _iso(now + timedelta(seconds=seconds))
    scope_text = str(data.get("scope") or "")
    scopes = [part for part in scope_text.split() if part]
    if existing is not None and not scopes:
        scopes = list(existing.scopes)
    access = str(data.get("access_token") or "").strip()
    if not access:
        raise OAuthError("Google did not return an access token.")
    refresh = str(data.get("refresh_token") or "").strip()
    if not refresh and existing is not None:
        refresh = existing.refresh_token
    token_type = str(data.get("token_type") or "Bearer") or "Bearer"
    who = email or (existing.email if existing is not None else "")
    connected_at = existing.connected_at if existing is not None else _iso(now)
    return AuthAccount(
        provider="google",
        access_token=access,
        refresh_token=refresh,
        token_type=token_type,
        expires_at=expires_at,
        scopes=scopes,
        email=who,
        connected_at=connected_at,
    )


def fetch_userinfo(
    access_token: str, *, http: requests.Session | None = None
) -> str:
    session = http or _http_session()
    response = session.get(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=TOKEN_TIMEOUT,
    )
    if response.status_code >= 400:
        return ""
    try:
        data = response.json()
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("email") or data.get("name") or "").strip()


def finish_google_login(
    store: AuthStore,
    login: GoogleLoginSession,
    code: str,
    *,
    http: requests.Session | None = None,
) -> AuthAccount:
    tokens = exchange_code(login, code, http=http)
    existing = store.get("google")
    account = account_from_token_response(tokens, existing=existing)
    email = fetch_userinfo(account.access_token, http=http)
    if email:
        account.email = email
    store.put(account)
    return account


def connect_google(
    store: AuthStore | None = None,
    *,
    timeout: float = CALLBACK_TIMEOUT,
    open_browser: bool = True,
    http: requests.Session | None = None,
    wait: Callable[[GoogleLoginSession, float], str] | None = None,
    announce: Callable[[str], None] | None = None,
) -> str:
    """Run the installed-app OAuth loop and persist tokens."""
    store = store or AuthStore()
    if not google_client_id():
        return missing_client_id_message()
    callback: LocalCallback | None = None
    try:
        callback = LocalCallback()
        login = begin_google_login(callback.redirect_uri)
        message = (
            "Open this URL to connect Google mail and calendar "
            "(readonly OAuth — not a second AI key):\n"
            f"{login.auth_url}"
        )
        if announce is not None:
            announce(message)
        else:
            print(message)
        if open_browser:
            try:
                webbrowser.open(login.auth_url)
            except Exception:
                pass
        waiter = wait or (
            lambda session, seconds: callback.wait_for_code(session.state, seconds)
        )
        code = waiter(login, timeout)
        account = finish_google_login(store, login, code, http=http)
    except OAuthError as exc:
        return str(exc)
    finally:
        if callback is not None:
            callback.close()
    who = account.email or "your Google account"
    return (
        f"Connected Google as {who}. Jarvis can read mail and calendar "
        "with this login — still no extra AI key."
    )


def disconnect_google(store: AuthStore | None = None) -> str:
    store = store or AuthStore()
    removed = store.disconnect("google")
    if removed is None:
        return "Google is not connected."
    who = removed.email or "Google"
    return f"Disconnected {who}. Mail and calendar need `--connect google` again."


class LocalCallback:
    """One-shot HTTP server on 127.0.0.1 that captures an OAuth redirect."""

    def __init__(self, bind: str = "127.0.0.1"):
        self._result: dict[str, str] = {}
        self._error: OAuthError | None = None
        self._httpd = HTTPServer((bind, 0), _make_handler(self))
        host, port = self._httpd.server_address[:2]
        self.redirect_uri = f"http://{host}:{port}/"
        self._thread: threading.Thread | None = None

    def wait_for_code(self, expected_state: str, timeout: float) -> str:
        self._expected_state = expected_state
        self._thread = threading.Thread(
            target=self._httpd.handle_request, daemon=True
        )
        self._thread.start()
        self._thread.join(timeout)
        if self._thread.is_alive():
            self._poke()
            self._thread.join(2)
            raise OAuthError(
                "Timed out waiting for Google login. "
                "Try again: python main.py --connect google"
            )
        if self._error is not None:
            raise self._error
        code = self._result.get("code", "")
        if not code:
            raise OAuthError("Google login did not return a code.")
        return code

    def close(self) -> None:
        try:
            self._httpd.server_close()
        except Exception:
            pass

    def _poke(self) -> None:
        try:
            requests.get(
                self.redirect_uri + "?error=timeout",
                timeout=1,
            )
        except Exception:
            pass


def _make_handler(owner: LocalCallback):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path not in {"/", "/callback", "/oauth2/callback"}:
                self.send_error(404)
                return
            try:
                code = parse_oauth_callback(
                    self.path, getattr(owner, "_expected_state", "")
                )
            except OAuthError as exc:
                owner._error = exc
                self._write(
                    400,
                    "Jarvis could not finish Google login. You can close this tab.",
                )
                return
            owner._result["code"] = code
            self._write(
                200,
                "Google is connected to Jarvis. You can close this tab.",
            )

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

        def _write(self, status: int, body: str) -> None:
            payload = (
                "<!doctype html><html><body><p>"
                f"{body}</p></body></html>"
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return Handler


def _token_request(
    payload: dict, *, http: requests.Session | None = None
) -> dict:
    session = http or _http_session()
    try:
        response = session.post(
            GOOGLE_TOKEN_URL,
            data=payload,
            timeout=TOKEN_TIMEOUT,
            headers={"Accept": "application/json"},
        )
    except requests.exceptions.RequestException as exc:
        raise OAuthError(f"Could not reach Google token endpoint: {exc}") from exc
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400 or not isinstance(data, dict):
        err = ""
        if isinstance(data, dict):
            err = str(data.get("error_description") or data.get("error") or "")
        raise OAuthError(
            err or f"Google token exchange failed (HTTP {response.status_code})."
        )
    return data


def _http_session() -> requests.Session:
    session = requests.Session()
    session.headers.setdefault("User-Agent", USER_AGENT)
    return session


def _urlsafe(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def ensure_fresh_google_account(
    store: AuthStore,
    *,
    http: requests.Session | None = None,
    now=None,
) -> AuthAccount:
    """Return a connected Google account, refreshing if the access token is stale."""
    account = store.get("google")
    if account is None or not account.access_token:
        raise OAuthError(
            "Google is not connected. Run: python main.py --connect google"
        )
    if not account.access_expired(now=now):
        return account
    refreshed = refresh_google_account(account, http=http)
    store.put(refreshed)
    return refreshed


# parse_iso is used by callers that check expiry in tests
__all__ = [
    "CALLBACK_TIMEOUT",
    "GOOGLE_SCOPES",
    "GoogleLoginSession",
    "LocalCallback",
    "OAuthError",
    "account_from_token_response",
    "begin_google_login",
    "build_authorization_url",
    "connect_google",
    "disconnect_google",
    "ensure_fresh_google_account",
    "exchange_code",
    "fetch_userinfo",
    "finish_google_login",
    "generate_pkce",
    "google_client_id",
    "google_client_secret",
    "missing_client_id_message",
    "new_state",
    "parse_oauth_callback",
    "refresh_google_account",
]
