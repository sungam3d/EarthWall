"""
Fetches current weather for each configured city, for optional display
next to the marker label on the wallpaper.

Uses Open-Meteo (https://open-meteo.com) - a genuinely free weather API
that doesn't require an API key or account, has reasonable rate limits
for personal use, and is fine to hit every ~15 minutes per city. The one
network dependency here is optional and cached; failures never break the
render, they just mean the weather part of a label doesn't show.

Concurrency and caching mirror the clouds module:
- Per-city in-memory cache with a max age
- Thread-safe (both the preview and full-res workers may hit us at once)
- Per-city retry backoff after failures, so being offline doesn't stall
  every render with a 4-second timeout PER city
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import requests


CACHE_MAX_AGE = 15 * 60          # weather rarely changes on a shorter scale
RETRY_BACKOFF = 2 * 60           # avoid hammering after a failed fetch
FETCH_TIMEOUT = 4.0

_lock = threading.Lock()
_cache: dict[tuple, "WeatherReading"] = {}  # keyed by (round(lat,2), round(lon,2))
_last_attempt: dict[tuple, float] = {}


# Open-Meteo's WMO weather-code -> (label, emoji) mapping. Only the codes
# it actually returns are included; anything else falls back to a neutral
# placeholder rather than an "unknown code" error.
_WMO_CODES = {
    0:  ("Clear", "☀"),
    1:  ("Mostly clear", "🌤"),
    2:  ("Partly cloudy", "⛅"),
    3:  ("Overcast", "☁"),
    45: ("Fog", "🌫"),
    48: ("Rime fog", "🌫"),
    51: ("Light drizzle", "🌦"),
    53: ("Drizzle", "🌦"),
    55: ("Heavy drizzle", "🌧"),
    56: ("Freezing drizzle", "🌧"),
    57: ("Freezing drizzle", "🌧"),
    61: ("Light rain", "🌦"),
    63: ("Rain", "🌧"),
    65: ("Heavy rain", "🌧"),
    66: ("Freezing rain", "🌧"),
    67: ("Freezing rain", "🌧"),
    71: ("Light snow", "🌨"),
    73: ("Snow", "🌨"),
    75: ("Heavy snow", "❄"),
    77: ("Snow grains", "🌨"),
    80: ("Rain showers", "🌦"),
    81: ("Rain showers", "🌧"),
    82: ("Heavy showers", "⛈"),
    85: ("Snow showers", "🌨"),
    86: ("Snow showers", "❄"),
    95: ("Thunderstorm", "⛈"),
    96: ("Thunderstorm + hail", "⛈"),
    99: ("Thunderstorm + hail", "⛈"),
}


@dataclass
class WeatherReading:
    temp_c: float
    code: int
    label: str
    emoji: str
    fetched_at: float

    def temp_display(self, units: str = "C") -> str:
        if units.upper() == "F":
            f = self.temp_c * 9 / 5 + 32
            return f"{f:.0f}°F"
        return f"{self.temp_c:.0f}°C"


def _fetch(lat: float, lon: float) -> WeatherReading | None:
    try:
        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current_weather": "true",
            },
            timeout=FETCH_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json().get("current_weather") or {}
        temp = float(data["temperature"])
        code = int(data.get("weathercode", -1))
        label, emoji = _WMO_CODES.get(code, ("--", ""))
        return WeatherReading(temp_c=temp, code=code, label=label,
                               emoji=emoji, fetched_at=time.time())
    except Exception:
        return None


def get_weather(lat: float, lon: float) -> WeatherReading | None:
    """Return the freshest cached weather for the given location, fetching
    a new reading if the cache is stale and we're not in a fetch cooldown.
    Never raises; returns None only if we've never had a reading yet."""
    key = (round(lat, 2), round(lon, 2))
    now = time.time()

    with _lock:
        cached = _cache.get(key)
        if cached is not None and (now - cached.fetched_at) < CACHE_MAX_AGE:
            return cached

        # Cache stale/missing: try a refresh, unless we failed very recently.
        last = _last_attempt.get(key, 0.0)
        if now - last < RETRY_BACKOFF:
            return cached  # may be a stale reading; still better than nothing

        _last_attempt[key] = now

    # Network call outside the lock so we don't block other cities.
    fresh = _fetch(lat, lon)

    with _lock:
        if fresh is not None:
            _cache[key] = fresh
            return fresh
        return _cache.get(key)


def clear_cache() -> None:
    """Wipe all cached weather - useful when the user changes units or
    for testing."""
    with _lock:
        _cache.clear()
        _last_attempt.clear()
