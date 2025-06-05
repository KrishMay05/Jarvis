import requests
import os
from .base_tool import Tool

class WeatherTool(Tool):
    def __init__(self):
        self.api_key = os.getenv("OPENWEATHER_API_KEY")
        self.base_url = "http://api.openweathermap.org/data/2.5/weather"

    def name(self) -> str:
        return "weather"

    def description(self) -> str:
        return "Get the current weather for a location"

    def use(self, args):
        if not isinstance(args, str):
            return "Please provide a location name as a string"
        
        params = {
            'q': args,
            'appid': self.api_key,
            'units': 'metric'
        }
        
        try:
            response = requests.get(self.base_url, params=params)
            response.raise_for_status()
            data = response.json()
            
            weather = {
                'temperature': data['main']['temp'],
                'description': data['weather'][0]['description'],
                'humidity': data['main']['humidity'],
                'wind_speed': data['wind']['speed']
            }
            
            return f"Current weather in {args}:\n" + \
                   f"Temperature: {weather['temperature']}°C\n" + \
                   f"Description: {weather['description']}\n" + \
                   f"Humidity: {weather['humidity']}%\n" + \
                   f"Wind Speed: {weather['wind_speed']} m/s"
                   
        except requests.exceptions.RequestException as e:
            return f"Error fetching weather data: {str(e)}"