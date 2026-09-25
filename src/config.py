"""Resolve LLM provider(s) from environment variables.

Jarvis is designed so you drop in one AI API key and the built-in tools
(weather, time, research, chat, memory, automations, computer/browser)
work without extra vendor accounts. A localhost web UI (`--serve`) uses
that same key. Mail and calendar use optional Google OAuth — connect via
auth, not a second AI key. Optional MCP servers add third-party tools
the same way — still no second AI key.

A second Gemini/OpenAI/Anthropic key is optional. If present, Jarvis can
fail over to it when the primary provider is down — still no tool keys.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


SUPPORTED_PROVIDERS = ("gemini", "openai", "anthropic")

_DEFAULT_MODELS = {
    "gemini": "gemini-2.0-flash",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-5",
}

_PROVIDER_KEY_VARS = {
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
}

_KEY_PREFIXES = (
    ("sk-ant-", "anthropic"),
    ("sk-", "openai"),
    ("AIza", "gemini"),
)

USER_AGENT = "JarvisPersonalAssistant/0.9 (+https://github.com/KrishMay05/Jarvis)"

_MIN_KEY_LEN = 12
_MAX_KEY_LEN = 512
_PLACEHOLDER_KEYS = frozenset(
    {
        "your-gemini-key",
        "your-openai-key",
        "your-anthropic-key",
        "your-api-key",
        "changeme",
        "replace-me",
        "paste-here",
        "xxx",
        "xxxx",
        "todo",
        "...",
        "sk-...",
        "sk-ant-...",
    }
)

GOOGLE_OAUTH_CLIENT_ID_VAR = "GOOGLE_OAUTH_CLIENT_ID"
GOOGLE_OAUTH_CLIENT_SECRET_VAR = "GOOGLE_OAUTH_CLIENT_SECRET"
_GOOGLE_CLIENT_SUFFIX = ".apps.googleusercontent.com"
_MIN_GOOGLE_CLIENT_LEN = 24
_MAX_GOOGLE_CLIENT_LEN = 256
_MIN_GOOGLE_SECRET_LEN = 8
_MAX_GOOGLE_SECRET_LEN = 512
_PLACEHOLDER_GOOGLE_IDS = frozenset(
    {
        "....apps.googleusercontent.com",
        "your-client-id.apps.googleusercontent.com",
        "example.apps.googleusercontent.com",
        "changeme.apps.googleusercontent.com",
        "client-id.apps.googleusercontent.com",
    }
)
_PLACEHOLDER_GOOGLE_SECRETS = frozenset(
    {
        "changeme",
        "your-client-secret",
        "client-secret",
        "secret",
        "xxx",
        "xxxx",
        "todo",
        "paste-here",
        "replace-me",
    }
)
_ENV_ASSIGNMENT = re.compile(
    r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$"
)


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    api_key: str
    model: str

    def summary(self) -> str:
        return f"{self.provider} ({self.model})"


class MissingAPIKeyError(RuntimeError):
    """Raised when no supported LLM API key is configured."""


class InvalidAPIKeyError(ValueError):
    """Raised when a pasted key is empty or not plausible."""


class InvalidGoogleClientError(ValueError):
    """Raised when a pasted Google OAuth client id is empty or implausible."""


def get_llm_settings() -> LLMSettings:
    """Pick the primary LLM provider from env.

    One key is enough. Extra provider keys become backups (see
    ``list_llm_settings``).

    Priority:
    1. ``JARVIS_LLM_PROVIDER`` plus that provider's key (or ``JARVIS_API_KEY``)
    2. The first standard key found: Gemini, OpenAI, then Anthropic
    3. ``JARVIS_API_KEY`` with a provider inferred from the key prefix
    """
    return list_llm_settings()[0]


def list_llm_settings() -> list[LLMSettings]:
    """Configured providers, preferred first.

    Still works with a single key. Additional Gemini / OpenAI / Anthropic
    keys are optional backups, not extra vendor accounts for tools.
    """
    explicit = (os.getenv("JARVIS_LLM_PROVIDER") or "").strip().lower()
    if explicit and explicit not in SUPPORTED_PROVIDERS:
        raise RuntimeError(
            f"Unknown JARVIS_LLM_PROVIDER '{explicit}'. "
            f"Use one of: {', '.join(SUPPORTED_PROVIDERS)}."
        )

    ordered: list[LLMSettings] = []
    seen: set[str] = set()

    def _add(provider: str, api_key: str, *, allow_override: bool) -> None:
        key = (api_key or "").strip()
        if not key or provider in seen:
            return
        seen.add(provider)
        ordered.append(
            LLMSettings(
                provider=provider,
                api_key=key,
                model=_model_for(provider, allow_override=allow_override),
            )
        )

    if explicit:
        api_key = _key_for_provider(explicit)
        if not api_key:
            raise MissingAPIKeyError(_missing_key_message(explicit))
        _add(explicit, api_key, allow_override=True)

    for name in SUPPORTED_PROVIDERS:
        _add(
            name,
            _first_env(_PROVIDER_KEY_VARS[name]),
            allow_override=not ordered,
        )

    shared = (os.getenv("JARVIS_API_KEY") or "").strip()
    if shared:
        inferred = _infer_provider(shared)
        _add(inferred, shared, allow_override=not ordered)

    if not ordered:
        raise MissingAPIKeyError(_missing_key_message(None))
    return ordered


def describe_runtime(settings: LLMSettings | None = None) -> str:
    """Human-readable setup summary for --status and the REPL banner.

    Works without an API key so first-run users can confirm tools, MCP,
    memory, automations, and auth before pasting a Gemini / OpenAI /
    Anthropic key. No live model call either way.
    """
    from src.auth.store import auth_status_line
    from src.automation.store import automation_status_line
    from src.mcp.config import mcp_status_line
    from src.memory.store import memory_status_line

    configured: list[LLMSettings] = []
    try:
        configured = list_llm_settings()
    except MissingAPIKeyError:
        configured = []
    settings = settings or (configured[0] if configured else None)
    if settings is None:
        llm_line = (
            "LLM: not configured — paste a Gemini, OpenAI, or Anthropic key "
            "in `python main.py --serve` or set GEMINI_API_KEY / OPENAI_API_KEY / "
            "ANTHROPIC_API_KEY (no live model call needed for this status)"
        )
    else:
        fallbacks = [
            item for item in configured if item.provider != settings.provider
        ]
        fallback_line = ""
        if fallbacks:
            names = ", ".join(item.summary() for item in fallbacks)
            fallback_line = (
                f"\nFallback LLM: {names} "
                "(optional extra key — used only if the primary provider fails)"
            )
        llm_line = f"LLM: {settings.summary()}{fallback_line}"
    return (
        f"{llm_line}\n"
        "Tools: weather (wttr.in), time (local clock), "
        "research (Wikipedia + public web), chat (your LLM), "
        "memory (local file), automations (local schedule; --serve fires them in the background; "
        "--run-due fires reminders without an AI key), "
        "computer (public web pages), mail/calendar (Google OAuth), "
        "web UI (localhost --serve; paste an AI key, optional backup key, or Google OAuth client ID, streaming chat, restored history, clear conversation, edit memory/automations/MCP, or Connect Google)\n"
        f"{mcp_status_line()}\n"
        f"{memory_status_line()}\n"
        f"{automation_status_line()}\n"
        f"{auth_status_line()}\n"
        "Extra API keys: none required for built-in tools"
    )


def _key_for_provider(provider: str) -> str:
    for var in _PROVIDER_KEY_VARS[provider]:
        value = (os.getenv(var) or "").strip()
        if value:
            return value
    return (os.getenv("JARVIS_API_KEY") or "").strip()


def _first_env(names: tuple[str, ...]) -> str:
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def _model_for(provider: str, *, allow_override: bool = True) -> str:
    if allow_override:
        override = (os.getenv("JARVIS_MODEL") or "").strip()
        if override:
            return override
    return _DEFAULT_MODELS[provider]


def _infer_provider(api_key: str) -> str:
    for prefix, provider in _KEY_PREFIXES:
        if api_key.startswith(prefix):
            return provider
    return "gemini"


def infer_provider(api_key: str) -> str:
    """Guess gemini / openai / anthropic from a key prefix."""
    return _infer_provider((api_key or "").strip())


def provider_key_var(provider: str) -> str:
    """Canonical .env variable for a supported provider."""
    return _PROVIDER_KEY_VARS[provider][0]


def env_file_path() -> Path:
    """Where a pasted key is persisted (gitignored .env)."""
    override = (os.getenv("JARVIS_ENV_PATH") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path.cwd() / ".env"


def normalize_api_key(api_key: str) -> str:
    """Strip and reject empty or placeholder keys. Never logs the value."""
    key = (api_key or "").strip()
    if not key:
        raise InvalidAPIKeyError("Paste an AI API key first.")
    if any(ch.isspace() for ch in key):
        raise InvalidAPIKeyError("That does not look like an API key.")
    if len(key) < _MIN_KEY_LEN:
        raise InvalidAPIKeyError("That key is too short to be a real API key.")
    if len(key) > _MAX_KEY_LEN:
        raise InvalidAPIKeyError("That key is too long.")
    if key.lower() in _PLACEHOLDER_KEYS:
        raise InvalidAPIKeyError("Paste a real API key, not a placeholder.")
    return key


def resolve_provider(api_key: str, provider: str | None = None) -> str:
    name = (provider or "").strip().lower()
    if not name or name in {"auto", "detect", "default"}:
        return infer_provider(api_key)
    if name not in SUPPORTED_PROVIDERS:
        raise InvalidAPIKeyError(
            f"Unknown provider '{provider}'. Use gemini, openai, anthropic, or auto."
        )
    return name


def apply_llm_key(api_key: str, provider: str | None = None) -> LLMSettings:
    """Set the key in this process so chat can start without a restart."""
    key = normalize_api_key(api_key)
    name = resolve_provider(key, provider)
    os.environ[provider_key_var(name)] = key
    os.environ["JARVIS_LLM_PROVIDER"] = name
    return get_llm_settings()


def persist_llm_key(
    api_key: str,
    provider: str | None = None,
    *,
    path: Path | str | None = None,
) -> Path:
    """Write the key into a local .env (mode 0600). Other variables stay intact."""
    key = normalize_api_key(api_key)
    name = resolve_provider(key, provider)
    target = Path(path).expanduser() if path else env_file_path()
    _upsert_env_file(
        target,
        {
            provider_key_var(name): key,
            "JARVIS_LLM_PROVIDER": name,
        },
    )
    return target


def install_llm_key(
    api_key: str,
    provider: str | None = None,
    *,
    path: Path | str | None = None,
) -> LLMSettings:
    """Apply then persist a pasted key. One key is still enough."""
    settings = apply_llm_key(api_key, provider)
    persist_llm_key(api_key, settings.provider, path=path)
    return settings


def apply_backup_llm_key(
    api_key: str,
    provider: str | None = None,
) -> LLMSettings:
    """Add a second provider key without changing the primary.

    Pins ``JARVIS_LLM_PROVIDER`` to the current primary so a Gemini backup
    cannot steal first place from an implicit OpenAI/Anthropic primary.
    """
    primary = get_llm_settings()
    key = normalize_api_key(api_key)
    name = resolve_provider(key, provider)
    if name == primary.provider:
        raise InvalidAPIKeyError(
            "A backup must be a different provider than the primary. "
            "Use Save key to replace the primary."
        )
    os.environ[provider_key_var(name)] = key
    os.environ["JARVIS_LLM_PROVIDER"] = primary.provider
    return LLMSettings(
        provider=name,
        api_key=key,
        model=_model_for(name, allow_override=False),
    )


def persist_backup_llm_key(
    api_key: str,
    provider: str | None = None,
    *,
    path: Path | str | None = None,
) -> Path:
    """Write an optional backup key into local .env (mode 0600)."""
    primary = get_llm_settings()
    key = normalize_api_key(api_key)
    name = resolve_provider(key, provider)
    if name == primary.provider:
        raise InvalidAPIKeyError(
            "A backup must be a different provider than the primary. "
            "Use Save key to replace the primary."
        )
    target = Path(path).expanduser() if path else env_file_path()
    _upsert_env_file(
        target,
        {
            provider_key_var(name): key,
            "JARVIS_LLM_PROVIDER": primary.provider,
        },
    )
    return target


def install_backup_llm_key(
    api_key: str,
    provider: str | None = None,
    *,
    path: Path | str | None = None,
) -> LLMSettings:
    """Apply then persist an optional backup key. Primary stays put."""
    backup = apply_backup_llm_key(api_key, provider)
    persist_backup_llm_key(api_key, backup.provider, path=path)
    return backup


def _strip_wrapping_quotes(value: str) -> str:
    text = (value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1].strip()
    return text


def normalize_google_client_id(client_id: str) -> str:
    """Strip and reject empty or placeholder Google OAuth client IDs."""
    cid = _strip_wrapping_quotes(client_id)
    if not cid:
        raise InvalidGoogleClientError("Paste a Google OAuth client ID first.")
    if any(ch.isspace() for ch in cid):
        raise InvalidGoogleClientError("That does not look like a Google OAuth client ID.")
    if len(cid) < _MIN_GOOGLE_CLIENT_LEN:
        raise InvalidGoogleClientError("That client ID is too short.")
    if len(cid) > _MAX_GOOGLE_CLIENT_LEN:
        raise InvalidGoogleClientError("That client ID is too long.")
    if not cid.endswith(_GOOGLE_CLIENT_SUFFIX):
        raise InvalidGoogleClientError(
            "Google OAuth client IDs end with .apps.googleusercontent.com."
        )
    if cid.lower() in _PLACEHOLDER_GOOGLE_IDS:
        raise InvalidGoogleClientError("Paste a real OAuth client ID, not a placeholder.")
    return cid


def normalize_google_client_secret(client_secret: str | None) -> str | None:
    """Optional secret for web OAuth clients. Empty means leave it unchanged."""
    if client_secret is None:
        return None
    secret = _strip_wrapping_quotes(str(client_secret))
    if not secret:
        return None
    if any(ch.isspace() for ch in secret):
        raise InvalidGoogleClientError(
            "That does not look like a Google OAuth client secret."
        )
    if secret.lower() in _PLACEHOLDER_GOOGLE_SECRETS:
        raise InvalidGoogleClientError("Paste a real OAuth client secret, not a placeholder.")
    if len(secret) < _MIN_GOOGLE_SECRET_LEN or len(secret) > _MAX_GOOGLE_SECRET_LEN:
        raise InvalidGoogleClientError("That client secret does not look valid.")
    return secret


def apply_google_oauth_config(
    client_id: str,
    client_secret: str | None = None,
) -> str:
    """Set Google OAuth app credentials in this process — no restart."""
    cid = normalize_google_client_id(client_id)
    secret = normalize_google_client_secret(client_secret)
    os.environ[GOOGLE_OAUTH_CLIENT_ID_VAR] = cid
    if secret is not None:
        os.environ[GOOGLE_OAUTH_CLIENT_SECRET_VAR] = secret
    return cid


def persist_google_oauth_config(
    client_id: str,
    client_secret: str | None = None,
    *,
    path: Path | str | None = None,
) -> Path:
    """Write Google OAuth app credentials into local .env (mode 0600)."""
    cid = normalize_google_client_id(client_id)
    secret = normalize_google_client_secret(client_secret)
    target = Path(path).expanduser() if path else env_file_path()
    assignments = {GOOGLE_OAUTH_CLIENT_ID_VAR: cid}
    if secret is not None:
        assignments[GOOGLE_OAUTH_CLIENT_SECRET_VAR] = secret
    _upsert_env_file(target, assignments)
    return target


def install_google_oauth_config(
    client_id: str,
    client_secret: str | None = None,
    *,
    path: Path | str | None = None,
) -> str:
    """Apply then persist a pasted Google OAuth client ID. Not a second AI key."""
    cid = apply_google_oauth_config(client_id, client_secret)
    persist_google_oauth_config(client_id, client_secret, path=path)
    return cid


def _upsert_env_file(path: Path, assignments: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = (
        original.splitlines()
        if original.strip()
        else [
            "# Local Jarvis keys — gitignored. Do not commit this file.",
            "",
        ]
    )
    pending = dict(assignments)
    written: list[str] = []
    for line in lines:
        parsed = _parse_env_assignment(line)
        if parsed and parsed[0] in pending:
            key = parsed[0]
            written.append(f"{key}={_format_env_value(pending.pop(key))}")
        else:
            written.append(line)
    if pending:
        if written and written[-1].strip():
            written.append("")
        for key, value in pending.items():
            written.append(f"{key}={_format_env_value(value)}")
    text = "\n".join(written).rstrip() + "\n"
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except Exception:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise


def _parse_env_assignment(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    match = _ENV_ASSIGNMENT.match(stripped)
    if not match:
        return None
    return match.group(1), match.group(2)


def _format_env_value(value: str) -> str:
    if value == "":
        return ""
    if all(ch.isalnum() or ch in "-_./+=:" for ch in value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _missing_key_message(provider: str | None) -> str:
    if provider:
        names = list(_PROVIDER_KEY_VARS[provider]) + ["JARVIS_API_KEY"]
        return f"No API key found for {provider}. Set one of: {', '.join(names)}."
    return (
        "No AI API key found. Paste one in the localhost UI "
        "(python main.py --serve) or drop it into .env:\n"
        "  GEMINI_API_KEY=...\n"
        "  OPENAI_API_KEY=...\n"
        "  ANTHROPIC_API_KEY=...\n"
        "or JARVIS_API_KEY=... with JARVIS_LLM_PROVIDER=gemini|openai|anthropic"
    )
