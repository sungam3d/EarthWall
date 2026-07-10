"""
Persistent app settings, stored as JSON at ~/.config/earthwall/settings.json.
Kept deliberately simple (a plain dict) rather than a database - there's
only ever one user's settings and the file is tiny.
"""
from __future__ import annotations

import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "earthwall"
SETTINGS_PATH = CONFIG_DIR / "settings.json"
CITIES_PATH = CONFIG_DIR / "cities.json"

DEFAULTS = {
    "map_set": "blue_marble_july",
    "center_lon": 0.0,
    "twilight_width_deg": 7.0,
    "night_darkness": 0.85,
    "interval_seconds": 300,
    "resolution": "auto",  # "auto" or [width, height]
    "autostart": False,
    "live_clouds": False,
    "cloud_opacity": 0.35,
    "cloud_density": 1.0,   # 1.0 = raw satellite coverage; lower thins the field
    "night_view": True,     # False = full daylight map, no terminator/night side
    "paused": False,
    "temp_units": "C",  # "C" or "F"
}


def load_settings() -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not SETTINGS_PATH.exists():
        save_settings(DEFAULTS)
        return dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH) as f:
            data = json.load(f)
        merged = dict(DEFAULTS)
        merged.update(data)
        return merged
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULTS)


def save_settings(settings: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2)


def load_cities() -> list[dict]:
    if not CITIES_PATH.exists():
        return []
    try:
        with open(CITIES_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save_cities(cities: list[dict]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CITIES_PATH, "w") as f:
        json.dump(cities, f, indent=2)
