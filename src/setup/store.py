"""Local first-run setup state — no extra API key required.

The localhost UI wizard writes ``~/.jarvis/setup.json`` so a returning
session does not nag. Profile fields become ordinary memory facts the
planner already understands (name, home city, units).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.memory.store import MemoryStore, jarvis_home

_SCHEMA_VERSION = 1

_NAME_PREFIX = "My name is "
_CITY_PREFIXES = ("Home city is ", "I live in ")
_UNITS = {
    "c": "Celsius",
    "celsius": "Celsius",
    "metric": "Celsius",
    "f": "Fahrenheit",
    "fahrenheit": "Fahrenheit",
    "imperial": "Fahrenheit",
}


class SetupProfileError(ValueError):
    """Invalid optional profile field from the first-run wizard."""


@dataclass(frozen=True)
class SetupState:
    completed: bool = False
    skipped: bool = False
    completed_at: str = ""

    def to_dict(self) -> dict:
        return {
            "version": _SCHEMA_VERSION,
            "completed": self.completed,
            "skipped": self.skipped,
            "completed_at": self.completed_at,
        }


def default_setup_path() -> Path:
    explicit = (os.getenv("JARVIS_SETUP_PATH") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return jarvis_home() / "setup.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_setup_state(path: Path | str | None = None) -> SetupState:
    target = Path(path).expanduser() if path is not None else default_setup_path()
    if not target.is_file():
        return SetupState()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return SetupState()
    if not isinstance(raw, dict):
        return SetupState()
    return SetupState(
        completed=bool(raw.get("completed")),
        skipped=bool(raw.get("skipped")),
        completed_at=str(raw.get("completed_at") or "").strip(),
    )


def mark_setup_complete(
    *,
    skipped: bool = False,
    path: Path | str | None = None,
) -> SetupState:
    """Persist that the first-run wizard is done. Local file only."""
    target = Path(path).expanduser() if path is not None else default_setup_path()
    previous = load_setup_state(target)
    state = SetupState(
        completed=True,
        skipped=bool(skipped),
        completed_at=previous.completed_at or _utc_now(),
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state.to_dict(), indent=2, ensure_ascii=False) + "\n"
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(target)
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return state


def setup_status_line(state: SetupState | None = None) -> str:
    current = state if state is not None else load_setup_state()
    if current.completed and current.skipped:
        return "Setup: skipped (reopen Setup in the localhost UI anytime)"
    if current.completed:
        return "Setup: complete (one AI key path — no extra vendor)"
    return "Setup: not started (first-run wizard in the localhost UI)"


def has_profile_facts(store: MemoryStore | None) -> bool:
    if store is None:
        return False
    return any(_fact_kind(fact.text) for fact in store.list_facts())


def apply_setup_profile(
    store: MemoryStore,
    *,
    name: str = "",
    city: str = "",
    units: str = "",
) -> list[str]:
    """Write conventional profile facts, replacing earlier wizard values."""
    messages: list[str] = []
    cleaned_name = _clip_field(name)
    cleaned_city = _clip_field(city)
    cleaned_units = str(units or "").strip()
    if cleaned_name:
        _forget_kind(store, "name")
        messages.append(store.remember(_NAME_PREFIX + cleaned_name))
    if cleaned_city:
        _forget_kind(store, "city")
        messages.append(store.remember("Home city is " + cleaned_city))
    if cleaned_units:
        label = _UNITS.get(cleaned_units.casefold())
        if label is None:
            raise SetupProfileError(
                "Temperature units must be Celsius or Fahrenheit (or leave blank)."
            )
        _forget_kind(store, "units")
        messages.append(store.remember(f"I prefer {label}"))
    if not messages:
        raise SetupProfileError("Add a name, home city, or temperature unit first.")
    return messages


def _clip_field(value: str) -> str:
    return " ".join(str(value or "").split())


def _fact_kind(text: str) -> str | None:
    folded = str(text or "").strip()
    if not folded:
        return None
    if folded.casefold().startswith(_NAME_PREFIX.casefold()):
        return "name"
    if any(folded.casefold().startswith(prefix.casefold()) for prefix in _CITY_PREFIXES):
        return "city"
    lower = folded.casefold()
    if lower in {"i prefer celsius", "i prefer fahrenheit"}:
        return "units"
    return None


def _forget_kind(store: MemoryStore, kind: str) -> None:
    for fact in list(store.list_facts()):
        if _fact_kind(fact.text) == kind:
            store.forget(fact.id)
