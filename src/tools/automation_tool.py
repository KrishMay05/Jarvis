"""Schedule, list, and cancel local automations — no extra API key."""

from __future__ import annotations

import re

from src.automation.schedule import ScheduleParseError, parse_schedule, strip_when_phrases
from src.automation.store import AutomationStore
from src.tools.base_tool import Tool

_SCHEDULE = frozenset(
    {"schedule", "add", "create", "set", "remind", "reminder", "run"}
)
_LIST = frozenset({"list", "show", "ls", "status", "what", "recall"})
_CANCEL = frozenset({"cancel", "delete", "remove", "drop", "forget", "stop"})
_PAUSE = frozenset({"pause", "disable", "off"})
_ENABLE = frozenset({"enable", "resume", "on", "unpause"})
_DUE = frozenset({"due", "pending"})

_RUN_HINT = re.compile(
    r"\b(research|look up|lookup|search|check weather|summarize|run)\b",
    re.IGNORECASE,
)
_REMIND_HINT = re.compile(r"\bremind(?:\s+me)?\b", re.IGNORECASE)


class AutomationTool(Tool):
    def __init__(self, store: AutomationStore | None = None):
        self.store = store or AutomationStore()

    def name(self) -> str:
        return "automation"

    def aliases(self):
        return ("schedule", "remind", "reminder", "automations")

    def description(self) -> str:
        return (
            "Schedule local reminders and recurring Jarvis prompts "
            "(research, weather, and so on). No extra API key. "
            "Args: remind in 10 minutes to <text>; "
            "every morning run <prompt>; list; cancel <id>; "
            "pause <id>; enable <id>."
        )

    def use(self, args) -> str:
        action, payload, extra = _parse_args(args)
        if action in _LIST:
            return self.store.format_list(payload or None)
        if action in _CANCEL:
            return self._cancel(payload)
        if action in _PAUSE:
            return self._set_enabled(payload, False)
        if action in _ENABLE:
            return self._set_enabled(payload, True)
        if action in _DUE:
            due = self.store.due_jobs()
            if not due:
                return "No automations are due right now."
            return f"{len(due)} due:\n" + "\n".join(job.summary_line() for job in due)
        if action in _SCHEDULE or payload:
            return self._schedule(action, payload, extra)
        return (
            "Use automation with remind in 10 minutes to <text>, "
            "every morning run <prompt>, list, or cancel <id>."
        )

    def _schedule(self, action: str, payload: str, extra: dict) -> str:
        when = str(extra.get("when") or extra.get("schedule") or payload).strip()
        kind = str(extra.get("kind") or "").strip().lower()
        if action in {"remind", "reminder"}:
            kind = kind or "remind"
        elif action == "run":
            kind = kind or "run"
        if kind not in {"remind", "run"}:
            kind = _infer_kind(when)

        tz_name = extra.get("timezone") or extra.get("tz")
        try:
            schedule = parse_schedule(
                when,
                tz_name=str(tz_name).strip() if tz_name else None,
            )
        except ScheduleParseError as exc:
            return str(exc)

        title = str(
            extra.get("title")
            or extra.get("message")
            or extra.get("prompt")
            or extra.get("text")
            or ""
        ).strip()
        if not title:
            title = strip_when_phrases(payload or when)
        if not title:
            return "Say what to remind you about, or which prompt to run."

        message = str(extra.get("message") or title).strip()
        prompt = str(extra.get("prompt") or (title if kind == "run" else "")).strip()
        try:
            job = self.store.add(
                kind=kind,
                title=title,
                schedule=schedule,
                message=message if kind == "remind" else "",
                prompt=prompt if kind == "run" else "",
            )
        except ValueError as exc:
            return str(exc)

        recur = "recurring " if job.is_recurring() else ""
        return (
            f"Scheduled {recur}{job.kind} ({job.id}): {job.title} "
            f"— next run {job.next_run_at}"
        )

    def _cancel(self, query: str) -> str:
        if not query:
            return "Say which automation to cancel (id or a word from the title)."
        removed = self.store.cancel(query)
        if not removed:
            return f"No automation matched '{query}'."
        lines = [f"Cancelled {len(removed)} automation(s):"]
        lines.extend(f"- ({job.id}) {job.title}" for job in removed)
        return "\n".join(lines)

    def _set_enabled(self, query: str, enabled: bool) -> str:
        verb = "Enabled" if enabled else "Paused"
        if not query:
            return f"Say which automation to {verb.lower()} (id or title)."
        changed = self.store.set_enabled(query, enabled)
        if not changed:
            return f"No automation matched '{query}'."
        lines = [f"{verb} {len(changed)} automation(s):"]
        lines.extend(job.summary_line() for job in changed)
        return "\n".join(lines)


def _infer_kind(text: str) -> str:
    if _REMIND_HINT.search(text):
        return "remind"
    if _RUN_HINT.search(text):
        return "run"
    return "remind"


def _parse_args(args) -> tuple[str, str, dict]:
    if args is None:
        return "list", "", {}
    if isinstance(args, dict):
        extra = dict(args)
        action = str(
            extra.get("action")
            or extra.get("op")
            or extra.get("command")
            or ""
        ).strip().lower()
        payload = (
            extra.get("when")
            or extra.get("text")
            or extra.get("query")
            or extra.get("input")
            or extra.get("args")
            or extra.get("id")
            or extra.get("message")
            or extra.get("prompt")
            or ""
        )
        payload_text = str(payload).strip()
        if not action and payload_text:
            return _split_command(payload_text) + (extra,)
        if not action:
            return "list", payload_text, extra
        return action, payload_text, extra
    return _split_command(str(args).strip()) + ({},)


def _split_command(text: str) -> tuple[str, str]:
    if not text:
        return "list", ""
    first, _, rest = text.partition(" ")
    verb = first.strip().lower().rstrip(":")
    if verb in _SCHEDULE | _LIST | _CANCEL | _PAUSE | _ENABLE | _DUE:
        return verb, rest.strip()
    return "schedule", text
