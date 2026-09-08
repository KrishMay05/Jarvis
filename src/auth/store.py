"""Local OAuth token store — connect via auth, not a second AI key.

Tokens live in ``~/.jarvis/auth.json`` (or ``JARVIS_AUTH_PATH`` /
``JARVIS_HOME``). The file is created with mode 0600.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.memory.store import jarvis_home

_SCHEMA_VERSION = 1


def default_auth_path() -> Path:
    explicit = (os.getenv("JARVIS_AUTH_PATH") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return jarvis_home() / "auth.json"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def parse_iso(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass
class AuthAccount:
    provider: str
    access_token: str = ""
    refresh_token: str = ""
    token_type: str = "Bearer"
    expires_at: str | None = None
    scopes: list[str] = field(default_factory=list)
    email: str = ""
    connected_at: str = field(default_factory=lambda: _iso(_utc_now()))

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_type": self.token_type,
            "expires_at": self.expires_at,
            "scopes": list(self.scopes),
            "email": self.email,
            "connected_at": self.connected_at,
        }

    @classmethod
    def from_dict(cls, raw: object) -> AuthAccount | None:
        if not isinstance(raw, dict):
            return None
        provider = str(raw.get("provider") or "").strip().lower()
        if not provider:
            return None
        scopes_raw = raw.get("scopes") or []
        scopes = [str(item).strip() for item in scopes_raw if str(item).strip()]
        return cls(
            provider=provider,
            access_token=str(raw.get("access_token") or ""),
            refresh_token=str(raw.get("refresh_token") or ""),
            token_type=str(raw.get("token_type") or "Bearer") or "Bearer",
            expires_at=str(raw.get("expires_at") or "").strip() or None,
            scopes=scopes,
            email=str(raw.get("email") or "").strip(),
            connected_at=str(raw.get("connected_at") or "").strip() or _iso(_utc_now()),
        )

    def access_expired(self, now: datetime | None = None, skew_seconds: int = 60) -> bool:
        if not self.access_token:
            return True
        expiry = parse_iso(self.expires_at or "")
        if expiry is None:
            return False
        moment = now or _utc_now()
        return moment + timedelta(seconds=skew_seconds) >= expiry

    def summary_line(self) -> str:
        who = self.email or "connected"
        return f"{self.provider}: {who} (readonly mail + calendar)"


class AuthStore:
    """Load, mutate, and persist connected OAuth accounts."""

    def __init__(self, path: Path | None = None):
        self.path = path if path is not None else default_auth_path()
        self.accounts: dict[str, AuthAccount] = {}
        self.load()

    def load(self) -> None:
        self.accounts = {}
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict):
            return
        blob = raw.get("accounts")
        if isinstance(blob, dict):
            items = blob.values()
        elif isinstance(blob, list):
            items = blob
        else:
            items = ()
        for item in items:
            parsed = AuthAccount.from_dict(item)
            if parsed is not None:
                self.accounts[parsed.provider] = parsed

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(self.path)
        try:
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    def to_dict(self) -> dict:
        return {
            "version": _SCHEMA_VERSION,
            "accounts": {
                name: account.to_dict() for name, account in self.accounts.items()
            },
        }

    def get(self, provider: str) -> AuthAccount | None:
        return self.accounts.get(str(provider or "").strip().lower())

    def put(self, account: AuthAccount) -> AuthAccount:
        key = account.provider.strip().lower()
        account.provider = key
        existing = self.accounts.get(key)
        if existing is not None and not account.connected_at:
            account.connected_at = existing.connected_at
        if existing is not None and existing.refresh_token and not account.refresh_token:
            account.refresh_token = existing.refresh_token
        if existing is not None and existing.email and not account.email:
            account.email = existing.email
        self.accounts[key] = account
        self.save()
        return account

    def disconnect(self, provider: str) -> AuthAccount | None:
        key = str(provider or "").strip().lower()
        removed = self.accounts.pop(key, None)
        if removed is not None:
            self.save()
        return removed

    def format_status(self) -> str:
        if not self.accounts:
            return (
                "Auth: no accounts connected. "
                "Run `python main.py --connect google` for mail and calendar "
                "(OAuth — not a second AI key)."
            )
        lines = ["Auth:"]
        for name in sorted(self.accounts):
            lines.append(f"- {self.accounts[name].summary_line()}")
        return "\n".join(lines)

    def status_line(self) -> str:
        if not self.accounts:
            return (
                f"Auth: none connected (tokens persist at {self.path} — "
                "OAuth, not an extra AI key)"
            )
        parts = [
            account.email or name for name, account in sorted(self.accounts.items())
        ]
        return f"Auth: {', '.join(parts)} at {self.path}"


def auth_status_line(store: AuthStore | None = None) -> str:
    if store is not None:
        return store.status_line()
    path = default_auth_path()
    if not path.is_file():
        return (
            f"Auth: none connected (tokens persist at {path} — "
            "OAuth, not an extra AI key)"
        )
    return AuthStore(path).status_line()
