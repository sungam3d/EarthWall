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
#
# Glyph choice: we use ONLY characters from the Basic Multilingual Plane
# (U+2600 - U+27FF, "Miscellaneous Symbols") that DejaVu Sans, Noto Sans,
# and Liberation Sans all include. The emoji-style U+1F3xx codepoints
# (🌤 🌦 🌧 etc) look nicer on systems that ship an emoji font, but on
# most Linux desktops they render as a `.notdef` box - which showed up
# as the "square where the icon should be" bug users hit for "Mostly
# clear" and other WMO codes 45+. These monochrome BMP glyphs render
# reliably everywhere.
_WMO_CODES = {
    0:  ("Clear", "\u2600"),            # ☀ sun
    1:  ("Mostly clear", "\u2600"),     # ☀ sun (clear enough)
    2:  ("Partly cloudy", "\u26C5"),    # ⛅ sun behind cloud (Misc Symbols)
    3:  ("Overcast", "\u2601"),         # ☁ cloud
    45: ("Fog", "\u2601"),              # ☁ cloud (fog has no BMP glyph)
    48: ("Rime fog", "\u2601"),
    51: ("Light drizzle", "\u2602"),    # ☂ umbrella
    53: ("Drizzle", "\u2602"),
    55: ("Heavy drizzle", "\u2614"),    # ☔ umbrella with rain
    56: ("Freezing drizzle", "\u2614"),
    57: ("Freezing drizzle", "\u2614"),
    61: ("Light rain", "\u2602"),
    63: ("Rain", "\u2614"),
    65: ("Heavy rain", "\u2614"),
    66: ("Freezing rain", "\u2614"),
    67: ("Freezing rain", "\u2614"),
    71: ("Light snow", "\u2744"),       # ❄ snowflake
    73: ("Snow", "\u2744"),
    75: ("Heavy snow", "\u2744"),
    77: ("Snow grains", "\u2744"),
    80: ("Rain showers", "\u2614"),
    81: ("Rain showers", "\u2614"),
    82: ("Heavy showers", "\u26A1"),    # ⚡ high voltage (heavy weather)
    85: ("Snow showers", "\u2744"),
    86: ("Snow showers", "\u2744"),
    95: ("Thunderstorm", "\u26A1"),     # ⚡ lightning
    96: ("Thunderstorm + hail", "\u26A1"),
    99: ("Thunderstorm + hail", "\u26A1"),
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
            headers={"User-Agent": "EarthWall/1.0 (Linux desktop wallpaper)"},
            timeout=FETCH_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json().get("current_weather") or {}
        temp = float(data["temperature"])
        code = int(data.get("weathercode", -1))
        label, emoji = _WMO_CODES.get(code, ("--", ""))
        return WeatherReading(temp_c=temp, code=code, label=label,
                               emoji=emoji, fetched_at=time.time())
    except Exception as e:
        # Print to stderr so users running from a terminal see WHY weather
        # isn't loading. Prior to this, silent failure was the second half
        # of the "weather not loading at all" problem: no reading appeared
        # and no error either, leaving the user with no way to diagnose.
        import sys
        print(f"[earthwall] weather fetch failed for ({lat},{lon}): {e}",
               file=sys.stderr)
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

        # Cache stale/missing: try a refresh, unless we failed very recently
        # AND we already have SOME cached reading to fall back on. Without
        # the second half of that condition, a first-time enable would hit
        # the backoff after the very first failure and never retry within
        # the 2-minute window, making weather look "not loading" even
        # though it's just waiting - a real bug reported by users.
        last = _last_attempt.get(key, 0.0)
        if now - last < RETRY_BACKOFF and cached is not None:
            return cached

        _last_attempt[key] = now

    # Network call outside the lock so we don't block other cities.
    fresh = _fetch(lat, lon)

    with _lock:
        if fresh is not None:
            _cache[key] = fresh
            return fresh
        return _cache.get(key)


def get_cached(lat: float, lon: float) -> WeatherReading | None:
    """Non-blocking read: return whatever is in the cache right now, or None.
    Used by the GUI to show status in tables without triggering a fetch."""
    key = (round(lat, 2), round(lon, 2))
    with _lock:
        return _cache.get(key)


def clear_cache() -> None:
    """Wipe all cached weather - useful when the user changes units or
    for testing."""
    with _lock:
        _cache.clear()
        _last_attempt.clear()
