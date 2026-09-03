from unittest.mock import Mock

import requests

from src.tools.weather_tool import WeatherTool


def test_weather_tool_formats_successful_response():
    session = Mock()
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "main": {"temp": 18.5, "humidity": 70},
        "weather": [{"description": "broken clouds"}],
        "wind": {"speed": 3.2},
    }
    session.get.return_value = response

    tool = WeatherTool(api_key="test-key", session=session)
    result = tool.use("London")

    session.get.assert_called_once()
    kwargs = session.get.call_args.kwargs
    assert kwargs["params"]["q"] == "London"
    assert kwargs["params"]["appid"] == "test-key"
    assert session.get.call_args.args[0].startswith("https://")
    assert "18.5°C" in result
    assert "broken clouds" in result
    assert "London" in result


def test_weather_tool_requires_location():
    result = WeatherTool(api_key="test-key").use("")
    assert "location" in result.lower()


def test_weather_tool_requires_api_key():
    result = WeatherTool(api_key="").use("Paris")
    assert "OPENWEATHER_API_KEY" in result


def test_weather_tool_handles_http_errors():
    session = Mock()
    session.get.side_effect = requests.exceptions.Timeout("timed out")
    result = WeatherTool(api_key="test-key", session=session).use("Berlin")
    assert result.startswith("Error fetching weather data:")
