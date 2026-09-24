"""First-run setup — one AI key, optional profile facts, no extra vendor."""

from src.setup.store import (
    SetupProfileError,
    SetupState,
    apply_setup_profile,
    default_setup_path,
    has_profile_facts,
    load_setup_state,
    mark_setup_complete,
    setup_status_line,
)

__all__ = [
    "SetupProfileError",
    "SetupState",
    "apply_setup_profile",
    "default_setup_path",
    "has_profile_facts",
    "load_setup_state",
    "mark_setup_complete",
    "setup_status_line",
]
