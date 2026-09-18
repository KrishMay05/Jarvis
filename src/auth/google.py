"""Read the user's Gmail and Calendar with a stored Google OAuth token."""

from __future__ import annotations

import base64
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
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
MAX_BODY_CHARS = 4000
_GMAIL_ID = re.compile(r"^[0-9a-fA-F]{6,32}$")
_WINDOW_ALIASES = {
    "today": "today",
    "agenda": "today",
    "tomorrow": "tomorrow",
    "week": "week",
    "this_week": "week",
    "this-week": "week",
    "thisweek": "week",
}


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
            lines.append(_format_mail_summary(detail, message_id))
        if len(lines) == 1:
            return f"No Google mail matched `{q}`."
        lines.append("Use `read <id>` to open the full body of a message.")
        return "\n".join(lines)
    except OAuthError as exc:
        return str(exc)


def get_mail(
    store: AuthStore | None = None,
    selector: str = "",
    *,
    http: requests.Session | None = None,
) -> str:
    """Read one Gmail message (full body) after Google OAuth — not an AI key."""
    store = store or AuthStore()
    session = http or _http_session()
    needle = (selector or "").strip()
    try:
        ensure_fresh_google_account(store, http=session)
        message_id = ""
        extra = ""
        if looks_like_gmail_id(needle):
            detail = _google_get(
                store,
                session,
                GMAIL_GET_URL.format(id=needle),
                params={"format": "full"},
                allow_missing=True,
            )
            if detail is not None:
                return _format_mail_body(detail, needle)
        query = needle or "in:inbox"
        data = _google_get(
            store,
            session,
            GMAIL_LIST_URL,
            params={"q": query, "maxResults": 5},
        )
        messages = data.get("messages") or []
        if not messages:
            return f"No Google mail matched `{query}`."
        message_id = str(messages[0].get("id") or "")
        if not message_id:
            return f"No Google mail matched `{query}`."
        detail = _google_get(
            store,
            session,
            GMAIL_GET_URL.format(id=message_id),
            params={"format": "full"},
        )
        if len(messages) > 1:
            extra = (
                f"\nShowing the first of {len(messages)} matches. "
                "Use `read <id>` for another."
            )
        return _format_mail_body(detail, message_id) + extra
    except OAuthError as exc:
        return str(exc)


def list_events(
    store: AuthStore | None = None,
    query: str = "",
    *,
    limit: int = 8,
    http: requests.Session | None = None,
    now: datetime | None = None,
    window: str | None = None,
) -> str:
    store = store or AuthStore()
    session = http or _http_session()
    try:
        account = ensure_fresh_google_account(store, http=session)
        moment = _as_local(now)
        q = (query or "").strip()
        kind = canonical_calendar_window(window) or canonical_calendar_window(q)
        if kind and canonical_calendar_window(q) == kind:
            q = ""
        time_min, time_max, label = calendar_bounds(kind, moment)
        params: dict[str, Any] = {
            "maxResults": max(1, min(int(limit), MAX_EVENTS)),
            "singleEvents": "true",
            "orderBy": "startTime",
            "timeMin": _rfc3339(time_min),
        }
        if time_max is not None:
            params["timeMax"] = _rfc3339(time_max)
        if q:
            params["q"] = q
        data = _google_get(
            store,
            session,
            CALENDAR_EVENTS_URL,
            params=params,
        )
        events = data.get("items") or []
        who = account.email or "Google Calendar"
        if not events:
            return _empty_events(who, label, q)
        heading = _events_heading(label, q)
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


def canonical_calendar_window(value: str | None) -> str | None:
    """Map user phrasing to today / tomorrow / week, or None for open upcoming."""
    if value is None:
        return None
    key = " ".join(str(value).strip().lower().split()).replace(" ", "_")
    return _WINDOW_ALIASES.get(key) or _WINDOW_ALIASES.get(key.replace("_", "-"))


def calendar_bounds(
    window: str | None, now: datetime
) -> tuple[datetime, datetime | None, str]:
    """Local-day windows so 'today' is an agenda, not an unbounded upcoming list."""
    local = _as_local(now)
    start_today = local.replace(hour=0, minute=0, second=0, microsecond=0)
    kind = (window or "").strip().lower()
    if kind == "today":
        return start_today, start_today + timedelta(days=1), "today"
    if kind == "tomorrow":
        start = start_today + timedelta(days=1)
        return start, start + timedelta(days=1), "tomorrow"
    if kind == "week":
        days_until_next_monday = 7 - start_today.weekday()
        return start_today, start_today + timedelta(days=days_until_next_monday), "this week"
    return local, None, "upcoming"


def _empty_events(who: str, label: str, query: str) -> str:
    if label == "upcoming":
        if query:
            return f"No upcoming events on {who} matching `{query}`."
        return f"No upcoming events on {who}."
    if query:
        return f"No events on {who} {label} matching `{query}`."
    return f"No events on {who} {label}."


def _events_heading(label: str, query: str) -> str:
    if label == "upcoming":
        heading = "Upcoming Google Calendar events"
    elif label == "today":
        heading = "Today on Google Calendar"
    elif label == "tomorrow":
        heading = "Tomorrow on Google Calendar"
    else:
        heading = "This week on Google Calendar"
    if query:
        heading += f" matching `{query}`"
    return heading + ":"


def looks_like_gmail_id(value: str) -> bool:
    text = (value or "").strip()
    return _GMAIL_ID.fullmatch(text) is not None


def _google_get(
    store: AuthStore,
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    retried: bool = False,
    allow_missing: bool = False,
) -> dict | None:
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
        return _google_get(
            store,
            session,
            url,
            params=params,
            retried=True,
            allow_missing=allow_missing,
        )
    if response.status_code == 404:
        if allow_missing:
            return None
        raise OAuthError("That Google mail message was not found.")
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


def _format_mail_summary(detail: dict, message_id: str) -> str:
    headers = _header_map(detail)
    subject = headers.get("subject") or "(no subject)"
    sender = headers.get("from") or "unknown sender"
    when = headers.get("date") or ""
    snippet = str(detail.get("snippet") or "").strip()
    ident = str(detail.get("id") or message_id).strip()
    line = f"- [{ident}] {subject} — {sender}"
    if when:
        line += f" ({_short_date(when)})"
    if snippet:
        line += f"\n  {snippet}"
    return line


def _format_mail_body(detail: dict, message_id: str) -> str:
    headers = _header_map(detail)
    subject = headers.get("subject") or "(no subject)"
    sender = headers.get("from") or "unknown sender"
    when = headers.get("date") or ""
    ident = str(detail.get("id") or message_id).strip()
    body = _extract_body(detail.get("payload") or {})
    if not body:
        body = str(detail.get("snippet") or "").strip() or "(no readable body)"
    if len(body) > MAX_BODY_CHARS:
        body = body[: MAX_BODY_CHARS - 1].rstrip() + "…"
    lines = [f"Google mail [{ident}] {subject} — {sender}"]
    if when:
        lines[0] += f" ({_short_date(when)})"
    lines.append(body)
    return "\n".join(lines)


def _extract_body(payload: dict) -> str:
    if not isinstance(payload, dict):
        return ""
    plain = _collect_mime(payload, "text/plain")
    if plain:
        return plain
    html = _collect_mime(payload, "text/html")
    if html:
        return _html_to_text(html)
    return ""


def _collect_mime(payload: dict, mime: str) -> str:
    wanted = mime.lower()
    chunks: list[str] = []

    def walk(node: dict) -> None:
        if not isinstance(node, dict):
            return
        kind = str(node.get("mimeType") or "").split(";")[0].strip().lower()
        body = node.get("body") or {}
        data = str(body.get("data") or "") if isinstance(body, dict) else ""
        if kind == wanted and data:
            chunks.append(_decode_b64(data))
            return
        for part in node.get("parts") or []:
            if isinstance(part, dict):
                walk(part)

    walk(payload)
    return "\n".join(part.strip() for part in chunks if part.strip()).strip()


def _decode_b64(data: str) -> str:
    text = "".join(str(data).split())
    if not text:
        return ""
    padded = text + "=" * ((4 - len(text) % 4) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        return ""
    return raw.decode("utf-8", errors="replace")


def _html_to_text(html: str) -> str:
    parser = _HtmlTextParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        pass
    return re.sub(r"[ \t]+\n", "\n", parser.text()).strip()


class _HtmlTextParser(HTMLParser):
    _SKIP = frozenset({"script", "style", "noscript"})
    _BREAK = frozenset({"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "blockquote"})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self._chunks: list[str] = []

    def text(self) -> str:
        return "".join(self._chunks)

    def handle_starttag(self, tag: str, attrs) -> None:
        name = tag.lower()
        if name in self._SKIP:
            self._skip += 1
            return
        if self._skip:
            return
        if name in self._BREAK:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name in self._SKIP and self._skip:
            self._skip -= 1
            return
        if self._skip:
            return
        if name in self._BREAK:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        self._chunks.append(data)


def _as_local(moment: datetime | None) -> datetime:
    """Treat tz-aware clocks as the user's local time; naive values use the host TZ."""
    if moment is None:
        return datetime.now().astimezone()
    if moment.tzinfo is None:
        return moment.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return moment


def _rfc3339(moment: datetime) -> str:
    utc = moment.astimezone(timezone.utc).replace(microsecond=0)
    return utc.isoformat().replace("+00:00", "Z")


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
