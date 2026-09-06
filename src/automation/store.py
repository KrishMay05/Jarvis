"""Persist scheduled jobs in a local JSON file — no extra API key."""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from src.automation.schedule import Schedule, next_after, utc_now
from src.memory.store import jarvis_home

_SCHEMA_VERSION = 1
_MAX_JOBS = 100
_MAX_TEXT = 500
_MAX_RESULT = 2000
_KINDS = frozenset({"remind", "run"})


def default_automations_path() -> Path:
    explicit = (os.getenv("JARVIS_AUTOMATIONS_PATH") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return jarvis_home() / "automations.json"


def _clip(text: str, limit: int) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


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
class AutomationJob:
    id: str
    kind: str
    title: str
    message: str = ""
    prompt: str = ""
    enabled: bool = True
    created_at: str = field(default_factory=lambda: _iso(utc_now()))
    next_run_at: str | None = None
    last_run_at: str | None = None
    last_result: str | None = None
    every_seconds: int | None = None
    daily_at: str | None = None
    timezone: str = "local"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "message": self.message,
            "prompt": self.prompt,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "next_run_at": self.next_run_at,
            "last_run_at": self.last_run_at,
            "last_result": self.last_result,
            "every_seconds": self.every_seconds,
            "daily_at": self.daily_at,
            "timezone": self.timezone,
        }

    @classmethod
    def from_dict(cls, raw: object) -> AutomationJob | None:
        if not isinstance(raw, dict):
            return None
        ident = str(raw.get("id") or "").strip()
        kind = str(raw.get("kind") or "").strip().lower()
        title = str(raw.get("title") or "").strip()
        if not ident or kind not in _KINDS or not title:
            return None
        every = raw.get("every_seconds")
        try:
            every_seconds = int(every) if every not in (None, "") else None
        except (TypeError, ValueError):
            every_seconds = None
        return cls(
            id=ident,
            kind=kind,
            title=_clip(title, _MAX_TEXT),
            message=_clip(str(raw.get("message") or ""), _MAX_TEXT),
            prompt=_clip(str(raw.get("prompt") or ""), _MAX_TEXT),
            enabled=bool(raw.get("enabled", True)),
            created_at=str(raw.get("created_at") or "").strip() or _iso(utc_now()),
            next_run_at=str(raw.get("next_run_at") or "").strip() or None,
            last_run_at=str(raw.get("last_run_at") or "").strip() or None,
            last_result=_clip(str(raw.get("last_result") or ""), _MAX_RESULT) or None,
            every_seconds=every_seconds,
            daily_at=str(raw.get("daily_at") or "").strip() or None,
            timezone=str(raw.get("timezone") or "local").strip() or "local",
        )

    def is_recurring(self) -> bool:
        return bool(self.every_seconds) or bool(self.daily_at)

    def is_due(self, now: datetime) -> bool:
        if not self.enabled or not self.next_run_at:
            return False
        when = parse_iso(self.next_run_at)
        if when is None:
            return False
        return when <= now

    def summary_line(self) -> str:
        state = "on" if self.enabled else "off"
        when = self.next_run_at or "done"
        recur = ""
        if self.every_seconds:
            recur = f", every {self.every_seconds}s"
        elif self.daily_at:
            recur = f", daily at {self.daily_at}"
        return (
            f"- ({self.id}) [{self.kind}/{state}] {self.title} "
            f"— next {when}{recur}"
        )


class AutomationStore:
    """Load, mutate, and persist scheduled jobs."""

    def __init__(self, path: Path | None = None):
        self.path = path if path is not None else default_automations_path()
        self.jobs: list[AutomationJob] = []
        self.load()

    def load(self) -> None:
        self.jobs = []
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict):
            return
        jobs: list[AutomationJob] = []
        for item in raw.get("jobs") or []:
            parsed = AutomationJob.from_dict(item)
            if parsed is not None:
                jobs.append(parsed)
        self.jobs = jobs[-_MAX_JOBS:]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(self.path)

    def to_dict(self) -> dict:
        return {
            "version": _SCHEMA_VERSION,
            "jobs": [job.to_dict() for job in self.jobs],
        }

    def add(
        self,
        *,
        kind: str,
        title: str,
        schedule: Schedule,
        message: str = "",
        prompt: str = "",
    ) -> AutomationJob:
        kind = str(kind or "").strip().lower()
        if kind not in _KINDS:
            raise ValueError("Job kind must be 'remind' or 'run'.")
        title_text = _clip(title, _MAX_TEXT)
        if not title_text:
            raise ValueError("Give the automation something to say or do.")
        if len(self.jobs) >= _MAX_JOBS:
            raise ValueError(f"At most {_MAX_JOBS} automations. Cancel one first.")
        job = AutomationJob(
            id=_new_id(),
            kind=kind,
            title=title_text,
            message=_clip(message, _MAX_TEXT),
            prompt=_clip(prompt, _MAX_TEXT),
            next_run_at=_iso(schedule.next_run_at),
            every_seconds=schedule.every_seconds,
            daily_at=schedule.daily_at,
            timezone=schedule.timezone,
        )
        self.jobs.append(job)
        self.save()
        return job

    def list_jobs(self, query: str | None = None) -> list[AutomationJob]:
        needle = str(query or "").strip()
        if not needle:
            return list(self.jobs)
        return [job for job in self.jobs if _job_matches(job, needle)]

    def find(self, query: str) -> list[AutomationJob]:
        return self.list_jobs(query)

    def cancel(self, query: str) -> list[AutomationJob]:
        needle = str(query or "").strip()
        if not needle:
            return []
        remaining: list[AutomationJob] = []
        removed: list[AutomationJob] = []
        for job in self.jobs:
            if _job_matches(job, needle):
                removed.append(job)
            else:
                remaining.append(job)
        if removed:
            self.jobs = remaining
            self.save()
        return removed

    def set_enabled(self, query: str, enabled: bool) -> list[AutomationJob]:
        changed: list[AutomationJob] = []
        for job in self.find(query):
            job.enabled = enabled
            changed.append(job)
        if changed:
            self.save()
        return changed

    def due_jobs(self, now: datetime | None = None) -> list[AutomationJob]:
        moment = now or utc_now()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return [job for job in self.jobs if job.is_due(moment)]

    def mark_ran(self, job: AutomationJob, result: str, now: datetime | None = None) -> None:
        moment = now or utc_now()
        job.last_run_at = _iso(moment)
        job.last_result = _clip(result, _MAX_RESULT) or None
        nxt = next_after(job, now=moment)
        if nxt is None:
            job.enabled = False
            job.next_run_at = None
        else:
            job.next_run_at = _iso(nxt)
        self.save()

    def format_list(self, query: str | None = None) -> str:
        jobs = self.list_jobs(query)
        if not jobs:
            if query:
                return f"No automations matched '{query}'."
            return (
                "No automations yet. Try 'remind me in 10 minutes to stretch' "
                "or 'every morning research the weather'."
            )
        lines = [f"{len(jobs)} automation(s):"]
        lines.extend(job.summary_line() for job in jobs)
        return "\n".join(lines)

    def status_line(self) -> str:
        total = len(self.jobs)
        active = sum(1 for job in self.jobs if job.enabled and job.next_run_at)
        due = len(self.due_jobs())
        location = self.path
        if total == 0:
            return (
                f"Automations: none scheduled (local file {location} — no extra API key)"
            )
        due_bit = f", {due} due" if due else ""
        return f"Automations: {active} scheduled ({total} total{due_bit}) at {location}"


def automation_status_line(store: AutomationStore | None = None) -> str:
    if store is not None:
        return store.status_line()
    path = default_automations_path()
    if not path.is_file():
        return f"Automations: none scheduled (local file {path} — no extra API key)"
    return AutomationStore(path).status_line()


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def _job_matches(job: AutomationJob, query: str) -> bool:
    needle = query.casefold()
    if job.id.casefold() == needle:
        return True
    haystacks = (job.title, job.message, job.prompt, job.kind)
    if any(needle in text.casefold() for text in haystacks if text):
        return True
    tokens = [token for token in re.split(r"\W+", needle) if token]
    blob = " ".join(haystacks).casefold()
    return bool(tokens) and all(token in blob for token in tokens)
