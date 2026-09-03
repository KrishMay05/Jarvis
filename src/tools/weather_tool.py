"""Current weather lookup via OpenWeatherMap."""

from __future__ import annotations

import os

import requests

from src.tools.base_tool import Tool


class WeatherTool(Tool):
    def __init__(self, api_key: str | None = None, session: requests.Session | None = None):
        self.api_key = api_key if api_key is not None else os.getenv("OPENWEATHER_API_KEY")
        self.base_url = "https://api.openweathermap.org/data/2.5/weather"
        self.session = session or requests.Session()

    def name(self) -> str:
        return "weather"

    def description(self) -> str:
        return "Get the current weather for a location"

    def use(self, args) -> str:
        location = _normalize_location(args)
        if not location:
            return "Please provide a location name as a string"

        if not self.api_key:
            return (
                "Weather lookup is not configured. "
                "Set OPENWEATHER_API_KEY in your environment or .env file."
            )

        params = {
            "q": location,
            "appid": self.api_key,
            "units": "metric",
        }

        try:
            response = self.session.get(self.base_url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.RequestException as exc:
            return f"Error fetching weather data: {exc}"

        try:
            temperature = data["main"]["temp"]
            description = data["weather"][0]["description"]
            humidity = data["main"]["humidity"]
            wind_speed = data["wind"]["speed"]
        except (KeyError, IndexError, TypeError):
            return f"Unexpected weather response for {location}."

        return (
            f"Current weather in {location}:\n"
            f"Temperature: {temperature}°C\n"
            f"Description: {description}\n"
            f"Humidity: {humidity}%\n"
            f"Wind Speed: {wind_speed} m/s"
        )


def _normalize_location(args) -> str | None:
    if args is None:
        return None
    if isinstance(args, dict):
        value = args.get("location") or args.get("city") or args.get("q") or args.get("input")
        return str(value).strip() if value else None
    text = str(args).strip()
    return text or None
