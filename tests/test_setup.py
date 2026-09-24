import json

import pytest

from src.memory.store import MemoryStore
from src.setup.store import (
    SetupProfileError,
    apply_setup_profile,
    default_setup_path,
    has_profile_facts,
    load_setup_state,
    mark_setup_complete,
    setup_status_line,
)


def test_setup_defaults_to_not_started(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_SETUP_PATH", str(tmp_path / "setup.json"))
    state = load_setup_state()
    assert state.completed is False
    assert state.skipped is False
    assert "not started" in setup_status_line(state)


def test_mark_setup_complete_persists_and_is_private(tmp_path, monkeypatch):
    path = tmp_path / "setup.json"
    monkeypatch.setenv("JARVIS_SETUP_PATH", str(path))
    state = mark_setup_complete()
    assert state.completed is True
    assert state.skipped is False
    assert state.completed_at
    assert path.is_file()
    assert (path.stat().st_mode & 0o777) == 0o600
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["completed"] is True
    reloaded = load_setup_state()
    assert reloaded.completed is True
    assert "complete" in setup_status_line(reloaded)


def test_mark_setup_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_SETUP_PATH", str(tmp_path / "setup.json"))
    state = mark_setup_complete(skipped=True)
    assert state.completed is True
    assert state.skipped is True
    assert "skipped" in setup_status_line(state)


def test_default_setup_path_uses_jarvis_home(tmp_path, monkeypatch):
    monkeypatch.delenv("JARVIS_SETUP_PATH", raising=False)
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "home"))
    assert default_setup_path() == tmp_path / "home" / "setup.json"


def test_apply_setup_profile_writes_conventional_facts(tmp_path):
    store = MemoryStore(tmp_path / "memory.json")
    messages = apply_setup_profile(
        store, name="Ada", city="Austin", units="celsius"
    )
    assert len(messages) == 3
    texts = [fact.text for fact in store.list_facts()]
    assert texts == [
        "My name is Ada",
        "Home city is Austin",
        "I prefer Celsius",
    ]
    assert has_profile_facts(store) is True


def test_apply_setup_profile_replaces_same_kind(tmp_path):
    store = MemoryStore(tmp_path / "memory.json")
    apply_setup_profile(store, name="Ada", city="Austin", units="c")
    apply_setup_profile(store, name="Krish", city="Seattle", units="fahrenheit")
    texts = [fact.text for fact in store.list_facts()]
    assert texts == [
        "My name is Krish",
        "Home city is Seattle",
        "I prefer Fahrenheit",
    ]


def test_apply_setup_profile_replaces_i_live_in_city(tmp_path):
    store = MemoryStore(tmp_path / "memory.json")
    store.remember("I live in Austin")
    apply_setup_profile(store, city="Denver")
    texts = [fact.text for fact in store.list_facts()]
    assert texts == ["Home city is Denver"]


def test_has_profile_facts_from_free_text_city(tmp_path):
    store = MemoryStore(tmp_path / "memory.json")
    assert has_profile_facts(store) is False
    store.remember("I live in Austin")
    assert has_profile_facts(store) is True


def test_apply_setup_profile_rejects_empty_and_bad_units(tmp_path):
    store = MemoryStore(tmp_path / "memory.json")
    with pytest.raises(SetupProfileError):
        apply_setup_profile(store)
    with pytest.raises(SetupProfileError):
        apply_setup_profile(store, units="kelvin")
    assert store.list_facts() == []
