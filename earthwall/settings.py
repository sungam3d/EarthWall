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
    "center_lat": 0.0,   # Phase 2 focal point Y - honoured once 2.6 lands
    "twilight_width_deg": 7.0,
    "night_darkness": 0.85,
    "interval_seconds": 300,
    "resolution": "auto",  # "auto" or [width, height]
    "autostart": False,
    "live_clouds": False,
    "cloud_opacity": 0.35,
    "cloud_density": 1.0,   # 1.0 = raw satellite coverage; lower thins the field
    "night_view": True,     # False = full daylight map, no terminator/night side
    "start_in_tray": False, # True = launch hidden in the system tray
    # Which profile (a named snapshot under profiles/) is currently
    # loaded. Empty string = no profile loaded, settings.json is being
    # used as an unnamed working draft.
    "active_profile": "",
    # --- Performance / power ---
    # Low usage mode: cap the render resolution and use cheaper resampling
    # so an update takes noticeably less CPU. Useful on laptops on battery
    # or on machines where a background app running a full-res LANCZOS
    # every N minutes is noticeable.
    "low_usage_mode": False,
    # Pause auto-updates while a fullscreen window (typically a game or a
    # video player) is active on any output. Detects fullscreen via the
    # standard X11 _NET_WM_STATE property.
    "pause_on_fullscreen": False,
    # --- Hazard overlays ---
    "show_earthquakes": False,
    "earthquake_min_mag": 4.5,     # only show quakes at/above this magnitude
    "earthquake_period": "week",   # hour | day | week | month
    "show_hurricanes": False,      # active tropical cyclones from NOAA NHC
    # How often (minutes) to pull FRESH hazard data from USGS/NOAA.
    # Between scans the overlay is drawn from the saved cache, so wallpaper
    # updates don't re-download every time. Default 30 min.
    "hazard_scan_minutes": 30,
    # Display customisation for the hazard overlays (keys match
    # render.DEFAULT_HAZARD_STYLE; only the ones the GUI exposes are set).
    "hazard_style": {
        "eq_shape": "circle",
        "eq_color_mode": "magnitude",
        "eq_color": "#FF3B30",
        "eq_size": 1.0,
        "eq_show_magnitude": False,
        "eq_mag_color": "#FFFFFF",
        "eq_mag_text_size": 1.0,
        "hur_shape": "spiral",
        "hur_color_mode": "category",
        "hur_color": "#E91EA0",
        "hur_size": 1.0,
        "hur_show_name": True,
        "hur_show_track": True,
    },
    # --- Multi-monitor (Phase 2) ---
    # "mirror"      = same map image on every monitor (default, back-compat)
    # "span"        = one wide image stretched across all monitors as one
    #                 virtual desktop; void areas (diagonal layouts, gaps)
    #                 painted with the primary monitor's void_fill.
    # "independent" = each monitor gets its own map view / zoom / pan.
    "monitors_mode": "mirror",
    "monitor_configs": {},  # keyed by monitor index (str) - see monitors.py
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


# ---------- import / export bundles ---------------------------------------

# The bundle format is a plain JSON object with two top-level keys plus a
# small header. Keeping it human-readable and stable across versions is
# deliberate: users may hand-edit these or share them between machines,
# and a rigid schema would just create upgrade pain later on.
EXPORT_SCHEMA_VERSION = 1


def export_bundle(settings: dict, cities: list[dict]) -> dict:
    """Build a JSON-serialisable dict containing everything that makes a
    user's EarthWall setup unique - settings AND cities plus any per-
    city notes / weather flags / label styling. Written to disk via
    ``json.dump`` in the GUI's export handler."""
    return {
        "kind": "earthwall-settings-bundle",
        "schema": EXPORT_SCHEMA_VERSION,
        "settings": dict(settings),
        "cities": list(cities),
    }


def import_bundle(bundle: dict) -> tuple[dict, list[dict]]:
    """Parse a bundle dict (typically loaded from a user's JSON file)
    into the (settings, cities) pair the app uses at runtime.

    We're liberal about what we accept: missing fields fall back to
    defaults, unexpected fields are ignored, and if the top-level shape
    is completely wrong (e.g. the file isn't ours at all) we raise a
    ValueError so the caller can show a friendly message rather than
    silently trashing the user's config with a half-loaded bundle."""
    if not isinstance(bundle, dict):
        raise ValueError("Not an EarthWall settings bundle (expected a JSON object).")
    if bundle.get("kind") != "earthwall-settings-bundle":
        raise ValueError(
            "Not an EarthWall settings bundle - the file doesn't have the "
            "expected 'kind' marker. Are you sure it was exported from EarthWall?"
        )
    settings_in = bundle.get("settings") or {}
    cities_in = bundle.get("cities") or []
    if not isinstance(settings_in, dict) or not isinstance(cities_in, list):
        raise ValueError("Bundle is malformed (settings must be an object, cities a list).")
    # Merge on top of DEFAULTS so any new keys the current version
    # expects are populated even if the bundle is from an older release.
    merged = dict(DEFAULTS)
    merged.update(settings_in)
    return merged, cities_in
