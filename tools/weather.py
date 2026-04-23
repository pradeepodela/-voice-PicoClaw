import httpx

from agent.tool_registry import tool

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

_WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Icy fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    80: "Slight showers", 81: "Moderate showers", 82: "Violent showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with heavy hail",
}


@tool(
    name="get_weather",
    description=(
        "Get the current weather for a city. "
        "Uses open-meteo.com — no API key required."
    ),
    parameters={
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "City name, e.g. 'London', 'New York', 'Bangalore'",
            },
            "units": {
                "type": "string",
                "description": "'celsius' or 'fahrenheit' (default: celsius)",
                "enum": ["celsius", "fahrenheit"],
            },
        },
        "required": ["city"],
    },
)
async def get_weather(city: str, units: str = "celsius") -> str:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Step 1: geocode
            geo = await client.get(
                _GEOCODE_URL,
                params={"name": city, "count": 1, "language": "en", "format": "json"},
            )
            geo.raise_for_status()
            results = geo.json().get("results", [])
            if not results:
                return f"Could not find location: {city}"

            loc = results[0]
            lat, lon = loc["latitude"], loc["longitude"]
            place = f"{loc['name']}, {loc.get('country', '')}"

            # Step 2: get weather
            temp_unit = "fahrenheit" if units == "fahrenheit" else "celsius"
            weather = await client.get(
                _WEATHER_URL,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weathercode",
                    "temperature_unit": temp_unit,
                    "wind_speed_unit": "kmh",
                    "forecast_days": 1,
                },
            )
            weather.raise_for_status()
            current = weather.json()["current"]

        temp = current["temperature_2m"]
        unit_sym = "°F" if units == "fahrenheit" else "°C"
        humidity = current["relative_humidity_2m"]
        wind = current["wind_speed_10m"]
        desc = _WMO_CODES.get(current.get("weathercode", 0), "Unknown")

        return (
            f"{place}: {desc}, {temp}{unit_sym}, "
            f"humidity {humidity}%, wind {wind} km/h"
        )
    except Exception as e:
        return f"Weather lookup failed: {e}"
