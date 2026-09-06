"""Parse reminder / recurring-job timing from short phrases.

No cron daemon and no extra API key — just datetimes stored locally.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_UNIT_SECONDS = {
    "s": 1,
    "sec": 1,
    "secs": 1,
    "second": 1,
    "seconds": 1,
    "m": 60,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,
    "h": 3600,
    "hr": 3600,
    "hrs": 3600,
    "hour": 3600,
    "hours": 3600,
    "d": 86400,
    "day": 86400,
    "days": 86400,
    "w": 604800,
    "week": 604800,
    "weeks": 604800,
}

_RELATIVE_RE = re.compile(
    r"\bin\s+(\d+)\s*"
    r"(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d|weeks?|w)\b",
    re.IGNORECASE,
)
_EVERY_RE = re.compile(
    r"\bevery\s+(\d+)\s*"
    r"(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d|weeks?|w)\b",
    re.IGNORECASE,
)
_EVERY_BARE_RE = re.compile(
    r"\bevery\s+(second|minute|hour|day|week)\b",
    re.IGNORECASE,
)
_DAILY_RE = re.compile(
    r"\b(?:daily|every\s+day)\b(?:\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?)?",
    re.IGNORECASE,
)
_NAMED_DAILY_RE = re.compile(
    r"\bevery\s+(morning|evening|night|noon)\b",
    re.IGNORECASE,
)
_AT_TIME_RE = re.compile(
    r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b",
    re.IGNORECASE,
)
_TOMORROW_RE = re.compile(r"\btomorrow\b", re.IGNORECASE)
_ISO_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?)\b"
)

_NAMED_HOURS = {
    "morning": (8, 0),
    "noon": (12, 0),
    "evening": (18, 0),
    "night": (21, 0),
}


@dataclass(frozen=True)
class Schedule:
    """When a job should next fire, plus optional recurrence."""

    next_run_at: datetime
    every_seconds: int | None = None
    daily_at: str | None = None
    timezone: str = "local"

    def is_recurring(self) -> bool:
        return self.every_seconds is not None or self.daily_at is not None


class ScheduleParseError(ValueError):
    """Raised when a phrase has no usable time."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def parse_schedule(text: str, *, now: datetime | None = None, tz_name: str | None = None) -> Schedule:
    """Turn a natural phrase into a concrete next-run time.

    Supported examples:
    - in 10 minutes
    - in 2 hours
    - tomorrow at 9am
    - at 15:30
    - every 30 minutes
    - every day
    - daily at 8:00
    - every morning
    - 2026-09-06T18:00:00Z
    """
    blob = str(text or "").strip()
    if not blob:
        raise ScheduleParseError("Say when to run it, for example 'in 10 minutes'.")

    now = _aware(now or utc_now())
    zone = _resolve_zone(tz_name)

    iso = _ISO_RE.search(blob)
    if iso:
        stamp = _parse_iso(iso.group(1))
        return Schedule(next_run_at=stamp, timezone=_zone_label(zone))

    every = _EVERY_RE.search(blob)
    if every:
        seconds = int(every.group(1)) * _seconds_for(every.group(2))
        if seconds < 1:
            raise ScheduleParseError("Repeat interval must be at least 1 second.")
        return Schedule(
            next_run_at=now + timedelta(seconds=seconds),
            every_seconds=seconds,
            timezone=_zone_label(zone),
        )

    bare = _EVERY_BARE_RE.search(blob)
    if bare:
        seconds = _seconds_for(bare.group(1))
        named = _NAMED_DAILY_RE.search(blob)
        if named:
            hour, minute = _NAMED_HOURS[named.group(1).lower()]
            return _daily_schedule(now, zone, hour, minute)
        if seconds >= 86400:
            clock = _AT_TIME_RE.search(blob)
            if clock:
                hour, minute = _clock(clock)
                return _daily_schedule(now, zone, hour, minute)
            return _daily_schedule(now, zone, 9, 0)
        return Schedule(
            next_run_at=now + timedelta(seconds=seconds),
            every_seconds=seconds,
            timezone=_zone_label(zone),
        )

    named = _NAMED_DAILY_RE.search(blob)
    if named:
        hour, minute = _NAMED_HOURS[named.group(1).lower()]
        return _daily_schedule(now, zone, hour, minute)

    daily = _DAILY_RE.search(blob)
    if daily:
        if daily.group(1) is not None:
            hour, minute = _clock(daily)
        else:
            clock = _AT_TIME_RE.search(blob)
            if clock:
                hour, minute = _clock(clock)
            else:
                hour, minute = 9, 0
        return _daily_schedule(now, zone, hour, minute)

    tomorrow = bool(_TOMORROW_RE.search(blob))
    clock = _AT_TIME_RE.search(blob)
    if tomorrow or clock:
        if clock:
            hour, minute = _clock(clock)
        else:
            hour, minute = 9, 0
        local = now.astimezone(zone)
        candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if tomorrow or candidate <= local:
            candidate += timedelta(days=1)
        return Schedule(next_run_at=candidate.astimezone(timezone.utc), timezone=_zone_label(zone))

    relative = _RELATIVE_RE.search(blob)
    if relative:
        seconds = int(relative.group(1)) * _seconds_for(relative.group(2))
        if seconds < 1:
            raise ScheduleParseError("Delay must be at least 1 second.")
        return Schedule(
            next_run_at=now + timedelta(seconds=seconds),
            timezone=_zone_label(zone),
        )

    raise ScheduleParseError(
        "Could not tell when to run that. Try 'in 10 minutes', "
        "'tomorrow at 9am', 'every 30 minutes', or 'daily at 8:00'."
    )


def next_after(schedule_like, *, now: datetime | None = None) -> datetime | None:
    """Compute the following run for a recurring job. None for one-shots."""
    now = _aware(now or utc_now())
    every = getattr(schedule_like, "every_seconds", None)
    daily_at = getattr(schedule_like, "daily_at", None)
    tz_name = getattr(schedule_like, "timezone", None)
    if every:
        return now + timedelta(seconds=int(every))
    if daily_at:
        hour, minute = _parse_hhmm(str(daily_at))
        return _daily_schedule(now, _resolve_zone(tz_name), hour, minute).next_run_at
    return None


def strip_when_phrases(text: str) -> str:
    """Remove timing words so the leftover is the reminder or prompt."""
    cleaned = str(text or "")
    for pattern in (
        _ISO_RE,
        _RELATIVE_RE,
        _EVERY_RE,
        _EVERY_BARE_RE,
        _NAMED_DAILY_RE,
        _DAILY_RE,
        _AT_TIME_RE,
        _TOMORROW_RE,
    ):
        cleaned = pattern.sub(" ", cleaned)
    cleaned = re.sub(
        r"\b(remind(?:\s+me)?|schedule|automation|please|to|that|i|me|should)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" :,-")


def _daily_schedule(now: datetime, zone, hour: int, minute: int) -> Schedule:
    local = now.astimezone(zone)
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    return Schedule(
        next_run_at=candidate.astimezone(timezone.utc),
        daily_at=f"{hour:02d}:{minute:02d}",
        timezone=_zone_label(zone),
    )


def _clock(match: re.Match) -> tuple[int, int]:
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    ampm = (match.group(3) or "").lower()
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    if hour == 24:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ScheduleParseError(f"Invalid clock time {hour}:{minute:02d}.")
    return hour, minute


def _parse_hhmm(value: str) -> tuple[int, int]:
    hour_s, _, minute_s = value.partition(":")
    hour = int(hour_s)
    minute = int(minute_s or 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ScheduleParseError(f"Invalid daily time '{value}'.")
    return hour, minute


def _seconds_for(unit: str) -> int:
    return _UNIT_SECONDS[unit.lower()]


def _aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _resolve_zone(name: str | None):
    label = (name or "").strip()
    if not label or label.lower() in {"local", "system"}:
        return datetime.now().astimezone().tzinfo or timezone.utc
    try:
        return ZoneInfo(label)
    except ZoneInfoNotFoundError as exc:
        raise ScheduleParseError(
            f"Unknown timezone '{label}'. Use an IANA name like America/Chicago."
        ) from exc


def _zone_label(zone) -> str:
    key = getattr(zone, "key", None)
    if key:
        return str(key)
    return "local"


def _parse_iso(value: str) -> datetime:
    stamp = value.strip()
    if stamp.endswith("Z"):
        stamp = stamp[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError as exc:
        raise ScheduleParseError(f"Invalid ISO datetime '{value}'.") from exc
    return _aware(parsed).replace(microsecond=0)
