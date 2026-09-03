"""Current-time lookup by IANA timezone."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.tools.base_tool import Tool


class TimeTool(Tool):
    def name(self) -> str:
        return "Time Tool"

    def description(self) -> str:
        return (
            "Provides the current time for a given city's timezone like "
            "Asia/Kolkata, America/New_York etc. If no timezone is provided, "
            "it returns the local time."
        )

    def use(self, args) -> str:
        timezone_name = _normalize_timezone(args)
        if not timezone_name:
            current_time = datetime.now().astimezone()
            return f"The current local time is {current_time.strftime('%Y-%m-%d %H:%M:%S %Z%z')}."

        try:
            current_time = datetime.now(ZoneInfo(timezone_name))
        except ZoneInfoNotFoundError:
            return (
                f"Unknown timezone '{timezone_name}'. "
                "Use an IANA name such as America/New_York or Asia/Kolkata."
            )

        return (
            f"The current time in {timezone_name} is "
            f"{current_time.strftime('%Y-%m-%d %H:%M:%S %Z%z')}."
        )


def _normalize_timezone(args) -> str | None:
    if args is None:
        return None
    if isinstance(args, dict):
        value = args.get("timezone") or args.get("tz") or args.get("city") or args.get("input")
        return str(value).strip() if value else None
    text = str(args).strip()
    return text or None
