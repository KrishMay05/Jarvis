import stat
import sys
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
import requests

from src.auth.google import list_events, list_mail
from src.auth.oauth import (
    OAuthError,
    begin_google_login,
    build_authorization_url,
    connect_google,
    disconnect_google,
    ensure_fresh_google_account,
    finish_google_login,
    generate_pkce,
    missing_client_id_message,
    parse_oauth_callback,
    refresh_google_account,
)
from src.auth.store import AuthAccount, AuthStore, auth_status_line
from src.tools.calendar_tool import CalendarTool
from src.tools.mail_tool import MailTool


def json_response(payload, status=200):
    resp = Mock()
    resp.status_code = status
    resp.json.return_value = payload
    return resp


def future_account(**overrides):
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    data = {
        "provider": "google",
        "access_token": "ya29.live",
        "refresh_token": "1//refresh",
        "token_type": "Bearer",
        "expires_at": later.replace(microsecond=0).isoformat(),
        "scopes": ["email"],
        "email": "ada@example.com",
    }
    data.update(overrides)
    return AuthAccount.from_dict(data)


def test_auth_store_roundtrip_and_permissions(tmp_path):
    path = tmp_path / "auth.json"
    store = AuthStore(path)
    store.put(
        AuthAccount(
            provider="google",
            access_token="ya29.a",
            refresh_token="1//r",
            email="ada@example.com",
        )
    )
    loaded = AuthStore(path)
    account = loaded.get("google")
    assert account is not None
    assert account.email == "ada@example.com"
    assert account.refresh_token == "1//r"
    mode = path.stat().st_mode
    assert stat.S_IMODE(mode) == 0o600
    assert "ada@example.com" in loaded.format_status()
    assert loaded.disconnect("google") is not None
    assert AuthStore(path).get("google") is None


def test_auth_status_line_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_AUTH_PATH", str(tmp_path / "missing.json"))
    text = auth_status_line()
    assert "none connected" in text
    assert "OAuth" in text


def test_pkce_and_auth_url(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "abc.apps.googleusercontent.com")
    verifier, challenge = generate_pkce()
    assert verifier
    assert challenge
    assert "=" not in verifier
    assert "=" not in challenge
    url = build_authorization_url(
        "http://127.0.0.1:9/",
        "state-1",
        challenge,
    )
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "code_challenge_method=S256" in url
    assert "access_type=offline" in url
    assert "gmail.readonly" in url
    assert "calendar.readonly" in url
    assert "abc.apps.googleusercontent.com" in url


def test_parse_oauth_callback_success_and_errors():
    assert parse_oauth_callback("/?code=xyz&state=s1", "s1") == "xyz"
    with pytest.raises(OAuthError, match="state"):
        parse_oauth_callback("/?code=xyz&state=nope", "s1")
    with pytest.raises(OAuthError, match="access_denied"):
        parse_oauth_callback("/?error=access_denied&state=s1", "s1")
    with pytest.raises(OAuthError, match="authorization code"):
        parse_oauth_callback("/?state=s1", "s1")


def test_begin_google_login_requires_client_id(monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    with pytest.raises(OAuthError, match="GOOGLE_OAUTH_CLIENT_ID"):
        begin_google_login("http://127.0.0.1:1/")


def test_finish_google_login_stores_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "abc.apps.googleusercontent.com")
    login = begin_google_login("http://127.0.0.1:9/")
    http = Mock()
    http.headers = {}
    http.post.return_value = json_response(
        {
            "access_token": "ya29.a",
            "refresh_token": "1//r",
            "expires_in": 3600,
            "token_type": "Bearer",
            "scope": "email",
        }
    )
    http.get.return_value = json_response({"email": "ada@example.com"})
    store = AuthStore(tmp_path / "auth.json")
    account = finish_google_login(store, login, "the-code", http=http)
    assert account.email == "ada@example.com"
    assert store.get("google").access_token == "ya29.a"
    posted = http.post.call_args
    assert posted.kwargs["data"]["code"] == "the-code"
    assert posted.kwargs["data"]["code_verifier"] == login.verifier


def test_connect_google_without_client_id():
    message = connect_google(AuthStore(), open_browser=False)
    assert "GOOGLE_OAUTH_CLIENT_ID" in message
    assert "not a second AI key" in missing_client_id_message()


def test_connect_google_with_injected_wait(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "abc.apps.googleusercontent.com")
    store = AuthStore(tmp_path / "auth.json")
    http = Mock()
    http.headers = {}
    http.post.return_value = json_response(
        {
            "access_token": "ya29.a",
            "refresh_token": "1//r",
            "expires_in": 3600,
            "scope": "email",
        }
    )
    http.get.return_value = json_response({"email": "ada@example.com"})
    notes: list[str] = []
    result = connect_google(
        store,
        open_browser=False,
        http=http,
        wait=lambda login, timeout: "code-from-browser",
        announce=notes.append,
    )
    assert "ada@example.com" in result
    assert notes and "accounts.google.com" in notes[0]
    assert disconnect_google(store) == (
        "Disconnected ada@example.com. Mail and calendar need `--connect google` again."
    )


def test_refresh_google_account(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "abc.apps.googleusercontent.com")
    account = future_account(access_token="old", expires_at="2020-01-01T00:00:00+00:00")
    http = Mock()
    http.headers = {}
    http.post.return_value = json_response(
        {"access_token": "new-token", "expires_in": 3600, "token_type": "Bearer"}
    )
    refreshed = refresh_google_account(account, http=http)
    assert refreshed.access_token == "new-token"
    assert refreshed.refresh_token == "1//refresh"


def test_ensure_fresh_refreshes_expired(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "abc.apps.googleusercontent.com")
    store = AuthStore(tmp_path / "auth.json")
    store.put(future_account(expires_at="2020-01-01T00:00:00+00:00"))
    http = Mock()
    http.headers = {}
    http.post.return_value = json_response(
        {"access_token": "fresh", "expires_in": 3600}
    )
    account = ensure_fresh_google_account(store, http=http)
    assert account.access_token == "fresh"


def test_list_mail_not_connected(tmp_path):
    store = AuthStore(tmp_path / "auth.json")
    text = list_mail(store)
    assert "not connected" in text.lower()
    assert "--connect google" in text


def test_list_mail_formats_messages(tmp_path):
    store = AuthStore(tmp_path / "auth.json")
    store.put(future_account())
    http = Mock()
    http.headers = {}

    def get(url, params=None, headers=None, timeout=None):
        if url.endswith("/messages") and "gmail" in url:
            return json_response({"messages": [{"id": "m1"}]})
        return json_response(
            {
                "id": "m1",
                "snippet": "See you at 3.",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Lunch"},
                        {"name": "From", "value": "Ada <ada@example.com>"},
                        {"name": "Date", "value": "Tue, 08 Sep 2026 12:00:00 +0000"},
                    ]
                },
            }
        )

    http.get.side_effect = get
    text = list_mail(store, "in:inbox", http=http)
    assert "Lunch" in text
    assert "Ada" in text
    assert "See you at 3." in text


def test_list_mail_retries_after_401(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "abc.apps.googleusercontent.com")
    store = AuthStore(tmp_path / "auth.json")
    store.put(future_account())
    http = Mock()
    http.headers = {}
    calls = {"n": 0}

    def get(url, params=None, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return json_response({"error": "invalid"}, status=401)
        return json_response({"messages": []})

    http.get.side_effect = get
    http.post.return_value = json_response(
        {"access_token": "rotated", "expires_in": 3600}
    )
    text = list_mail(store, http=http)
    assert "No Google mail" in text
    assert store.get("google").access_token == "rotated"


def test_list_events_formats_calendar(tmp_path):
    store = AuthStore(tmp_path / "auth.json")
    store.put(future_account())
    http = Mock()
    http.headers = {}
    http.get.return_value = json_response(
        {
            "items": [
                {
                    "summary": "Standup",
                    "start": {"dateTime": "2026-09-08T15:00:00+00:00"},
                    "location": "Zoom",
                },
                {
                    "summary": "Away",
                    "start": {"date": "2026-09-09"},
                },
            ]
        }
    )
    text = list_events(
        store,
        http=http,
        now=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )
    assert "Standup" in text
    assert "Zoom" in text
    assert "Away" in text
    assert "all day" in text


def test_mail_and_calendar_tools(tmp_path):
    store = AuthStore(tmp_path / "auth.json")
    mail = MailTool(store, http=Mock(headers={}, get=Mock()))
    assert "not connected" in mail.use("inbox").lower()
    calendar = CalendarTool(store)
    assert "not connected" in calendar.use("upcoming").lower()
    store.put(future_account())
    http = Mock()
    http.headers = {}
    http.get.return_value = json_response({"messages": []})
    assert "No Google mail" in MailTool(store, http=http).use("unread")
    http.get.return_value = json_response({"items": []})
    assert "No upcoming events" in CalendarTool(store, http=http).use(
        {"action": "search", "query": "standup"}
    )


def test_local_callback_captures_code():
    import time

    from src.auth.oauth import LocalCallback

    callback = LocalCallback()
    state = "csrf-token"
    result: dict[str, str] = {}
    errors: list[BaseException] = []

    def serve():
        try:
            result["code"] = callback.wait_for_code(state, timeout=5)
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    server = threading.Thread(target=serve)
    try:
        server.start()
        response = None
        last_exc: Exception | None = None
        for _ in range(40):
            try:
                response = requests.get(
                    callback.redirect_uri,
                    params={"code": "from-google", "state": state},
                    timeout=5,
                )
                break
            except requests.exceptions.ConnectionError as exc:
                last_exc = exc
                time.sleep(0.05)
        assert response is not None, last_exc
        assert response.status_code == 200
        server.join(5)
        assert not errors
        assert result.get("code") == "from-google"
    finally:
        callback.close()


def test_main_auth_without_api_key(monkeypatch, capsys):
    for var in (
        "JARVIS_LLM_PROVIDER",
        "JARVIS_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(sys, "argv", ["main.py", "--auth"])
    from main import main

    main()
    out = capsys.readouterr().out
    assert "no accounts connected" in out.lower() or "Auth:" in out


def test_main_connect_without_client_id(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["main.py", "--connect", "google"])
    from main import main

    main()
    out = capsys.readouterr().out
    assert "GOOGLE_OAUTH_CLIENT_ID" in out


def test_main_disconnect_when_empty(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["main.py", "--disconnect"])
    from main import main

    main()
    assert "not connected" in capsys.readouterr().out.lower()
