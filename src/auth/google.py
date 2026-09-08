"""Read the user's Gmail and Calendar with a stored Google OAuth token."""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import requests

from src.auth.oauth import (
    OAuthError,
    ensure_fresh_google_account,
    refresh_google_account,
)
from src.auth.store import AuthStore
from src.config import USER_AGENT

GMAIL_LIST_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
GMAIL_GET_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/{id}"
CALENDAR_EVENTS_URL = (
    "https://www.googleapis.com/calendar/v3/calendars/primary/events"
)

API_TIMEOUT = 20
MAX_MAIL = 8
MAX_EVENTS = 8


def list_mail(
    store: AuthStore | None = None,
    query: str = "",
    *,
    limit: int = 5,
    http: requests.Session | None = None,
) -> str:
    store = store or AuthStore()
    session = http or _http_session()
    try:
        ensure_fresh_google_account(store, http=session)
        params: dict[str, Any] = {
            "maxResults": max(1, min(int(limit), MAX_MAIL)),
        }
        q = (query or "").strip() or "in:inbox"
        params["q"] = q
        data = _google_get(
            store,
            session,
            GMAIL_LIST_URL,
            params=params,
        )
        messages = data.get("messages") or []
        if not messages:
            return f"No Google mail matched `{q}`."
        lines = [f"Recent Google mail (`{q}`):"]
        for item in messages[: params["maxResults"]]:
            message_id = str(item.get("id") or "")
            if not message_id:
                continue
            detail = _google_get(
                store,
                session,
                GMAIL_GET_URL.format(id=message_id),
                params={
                    "format": "metadata",
                    "metadataHeaders": ["From", "Subject", "Date"],
                },
            )
            headers = _header_map(detail)
            subject = headers.get("subject") or "(no subject)"
            sender = headers.get("from") or "unknown sender"
            when = headers.get("date") or ""
            snippet = str(detail.get("snippet") or "").strip()
            line = f"- {subject} — {sender}"
            if when:
                line += f" ({_short_date(when)})"
            if snippet:
                line += f"\n  {snippet}"
            lines.append(line)
        if len(lines) == 1:
            return f"No Google mail matched `{q}`."
        return "\n".join(lines)
    except OAuthError as exc:
        return str(exc)


def list_events(
    store: AuthStore | None = None,
    query: str = "",
    *,
    limit: int = 8,
    http: requests.Session | None = None,
    now: datetime | None = None,
) -> str:
    store = store or AuthStore()
    session = http or _http_session()
    try:
        account = ensure_fresh_google_account(store, http=session)
        moment = now or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        params: dict[str, Any] = {
            "maxResults": max(1, min(int(limit), MAX_EVENTS)),
            "singleEvents": "true",
            "orderBy": "startTime",
            "timeMin": moment.astimezone(timezone.utc)
            .replace(microsecond=0)
            .isoformat(),
        }
        q = (query or "").strip()
        if q:
            params["q"] = q
        data = _google_get(
            store,
            session,
            CALENDAR_EVENTS_URL,
            params=params,
        )
        events = data.get("items") or []
        if not events:
            who = account.email or "Google Calendar"
            if q:
                return f"No upcoming events on {who} matching `{q}`."
            return f"No upcoming events on {who}."
        heading = "Upcoming Google Calendar events"
        if q:
            heading += f" matching `{q}`"
        heading += ":"
        lines = [heading]
        for event in events[: params["maxResults"]]:
            title = str(event.get("summary") or "(no title)").strip()
            start = _event_when(event.get("start") or {})
            location = str(event.get("location") or "").strip()
            line = f"- {title} — {start}"
            if location:
                line += f" @ {location}"
            lines.append(line)
        return "\n".join(lines)
    except OAuthError as exc:
        return str(exc)


def _google_get(
    store: AuthStore,
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    retried: bool = False,
) -> dict:
    account = store.get("google")
    if account is None or not account.access_token:
        raise OAuthError(
            "Google is not connected. Run: python main.py --connect google"
        )
    headers = {"Authorization": f"{account.token_type} {account.access_token}"}
    query = _flatten_params(params or {})
    try:
        response = session.get(
            url,
            params=query,
            headers=headers,
            timeout=API_TIMEOUT,
        )
    except requests.exceptions.RequestException as exc:
        raise OAuthError(f"Could not reach Google: {exc}") from exc
    if response.status_code == 401 and not retried:
        refreshed = refresh_google_account(account, http=session)
        store.put(refreshed)
        return _google_get(store, session, url, params=params, retried=True)
    if response.status_code == 403:
        raise OAuthError(
            "Google denied access. Re-connect with Gmail and Calendar scopes: "
            "python main.py --connect google"
        )
    if response.status_code >= 400:
        raise OAuthError(f"Google API returned HTTP {response.status_code}.")
    try:
        data = response.json()
    except ValueError as exc:
        raise OAuthError("Google returned a non-JSON response.") from exc
    if not isinstance(data, dict):
        raise OAuthError("Google returned an unexpected payload.")
    return data


def _flatten_params(params: dict[str, Any]) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for key, value in params.items():
        if isinstance(value, (list, tuple)):
            for part in value:
                items.append((key, str(part)))
        elif isinstance(value, bool):
            items.append((key, "true" if value else "false"))
        else:
            items.append((key, str(value)))
    return items


def _header_map(message: dict) -> dict[str, str]:
    payload = message.get("payload") or {}
    headers = payload.get("headers") or []
    mapped: dict[str, str] = {}
    for item in headers:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip().lower()
        value = str(item.get("value") or "").strip()
        if name and value:
            mapped[name] = value
    return mapped


def _short_date(value: str) -> str:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        parsed = None
    if parsed is None:
        return value[:22]
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.strftime("%Y-%m-%d %H:%M")


def _event_when(start: dict) -> str:
    if not isinstance(start, dict):
        return "unspecified time"
    date_time = str(start.get("dateTime") or "").strip()
    day = str(start.get("date") or "").strip()
    raw = date_time or day
    if not raw:
        return "unspecified time"
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return date_time or day
    if date_time:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.strftime("%Y-%m-%d %H:%M")
    return parsed.strftime("%Y-%m-%d") + " (all day)"


def _http_session() -> requests.Session:
    session = requests.Session()
    session.headers.setdefault("User-Agent", USER_AGENT)
    return session


