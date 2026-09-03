from unittest.mock import Mock

import requests

from src.tools.weather_tool import WeatherTool


def test_weather_tool_formats_successful_response():
    session = Mock()
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "current_condition": [
            {
                "temp_C": "18",
                "FeelsLikeC": "17",
                "humidity": "70",
                "windspeedKmph": "12",
                "weatherDesc": [{"value": "Broken clouds"}],
            }
        ],
        "nearest_area": [
            {
                "areaName": [{"value": "London"}],
                "country": [{"value": "United Kingdom"}],
            }
        ],
    }
    session.get.return_value = response
    session.headers = {}

    tool = WeatherTool(session=session)
    result = tool.use("London")

    session.get.assert_called_once()
    url = session.get.call_args.args[0]
    assert url.startswith("https://wttr.in/")
    assert "London" in url
    assert "format=j1" in url
    assert "18°C" in result
    assert "Broken clouds" in result
    assert "London" in result
    assert "OPENWEATHER" not in result


def test_weather_tool_requires_location():
    session = Mock()
    session.headers = {}
    result = WeatherTool(session=session).use("")
    assert "location" in result.lower()
    session.get.assert_not_called()


def test_weather_tool_accepts_dict_args():
    session = Mock()
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "current_condition": [
            {
                "temp_C": "5",
                "humidity": "80",
                "windspeedKmph": "20",
                "weatherDesc": [{"value": "Snow"}],
            }
        ],
        "nearest_area": [],
    }
    session.get.return_value = response
    session.headers = {}

    result = WeatherTool(session=session).use({"city": "Oslo"})
    assert "Oslo" in result or "5°C" in result
    assert "Snow" in result


def test_weather_tool_handles_http_errors():
    session = Mock()
    session.headers = {}
    session.get.side_effect = requests.exceptions.Timeout("timed out")
    result = WeatherTool(session=session).use("Berlin")
    assert result.startswith("Error fetching weather data:")
