"""Current weather lookup via wttr.in — no extra API key required."""

from __future__ import annotations

from urllib.parse import quote

import requests

from src.config import USER_AGENT
from src.tools.base_tool import Tool


class WeatherTool(Tool):
    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def name(self) -> str:
        return "weather"

    def description(self) -> str:
        return "Get the current weather for a location. No extra API key needed."

    def use(self, args) -> str:
        location = _normalize_location(args)
        if not location:
            return "Please provide a location name as a string"

        url = f"https://wttr.in/{quote(location)}?format=j1"
        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.RequestException as exc:
            return f"Error fetching weather data: {exc}"
        except ValueError:
            return f"Unexpected weather response for {location}."

        try:
            current = data["current_condition"][0]
            temperature = current["temp_C"]
            description = current["weatherDesc"][0]["value"]
            humidity = current["humidity"]
            wind_speed = current.get("windspeedKmph", "?")
            feels_like = current.get("FeelsLikeC")
        except (KeyError, IndexError, TypeError):
            return f"Unexpected weather response for {location}."

        place = _place_label(data) or location
        lines = [
            f"Current weather in {place}:",
            f"Temperature: {temperature}°C",
            f"Description: {description}",
            f"Humidity: {humidity}%",
            f"Wind Speed: {wind_speed} km/h",
        ]
        if feels_like:
            lines.insert(2, f"Feels like: {feels_like}°C")
        return "\n".join(lines)


def _place_label(data: dict) -> str | None:
    try:
        area = data["nearest_area"][0]
        name = area["areaName"][0]["value"]
        country = area["country"][0]["value"]
        return f"{name}, {country}"
    except (KeyError, IndexError, TypeError):
        return None


def _normalize_location(args) -> str | None:
    if args is None:
        return None
    if isinstance(args, dict):
        value = args.get("location") or args.get("city") or args.get("q") or args.get("input")
        return str(value).strip() if value else None
    text = str(args).strip()
    return text or None
