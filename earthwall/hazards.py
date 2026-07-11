"""
Optional map overlays for live natural-hazard data:

- Earthquakes from the USGS Earthquake Hazards Program (public, no-auth
  GeoJSON). We use the pre-built CDN-cached summary feeds when the chosen
  magnitude/period bucket matches one exactly (fastest, ~1 min fresh),
  and fall back to the parameterised FDSN query for arbitrary
  magnitude+window combinations.
- Active tropical cyclones (hurricanes/typhoons) from NOAA's National
  Hurricane Center CurrentStorms.json index plus the per-storm Cc best-
  track / forecast GeoJSON the NHC map itself renders from.

Robustness rules mirror clouds.py / weather.py (learned the hard way):

1. Everything is cached to disk with a short TTL and served from an
   in-memory copy, so the render threads never block on the network and
   a brief outage doesn't wipe the overlay - the last good data keeps
   showing until a fetch succeeds.
2. All fetch/cache work is serialised behind a lock; the preview worker
   and full-res worker can both ask at once.
3. Disk cache writes are atomic (temp file + os.replace) so a reader can
   never observe a half-written file.
4. A failed fetch backs off rather than hammering the server, and NEVER
   raises into the renderer - the overlay is a nice-to-have, not
   something worth failing a wallpaper render over.

All network access is best-effort. If USGS/NOAA is unreachable the
functions return an empty list (or the last cached data), and the
renderer simply draws no hazard markers.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

CACHE_DIR = Path.home() / ".cache" / "earthwall"

# ---- Earthquakes ---------------------------------------------------------

USGS_SUMMARY = ("https://earthquake.usgs.gov/earthquakes/feed/v1.0/"
                "summary/{mag}_{period}.geojson")
USGS_QUERY = "https://earthquake.usgs.gov/fdsnws/event/1/query"

# The pre-built summary feeds exist only for these exact buckets.
_SUMMARY_MAGS = {"significant", "4.5", "2.5", "1.0", "all"}
_SUMMARY_PERIODS = {"hour", "day", "week", "month"}

# Map the GUI's period choices to (summary-period, seconds) pairs.
PERIOD_SECONDS = {
    "hour": 3600,
    "day": 86400,
    "week": 7 * 86400,
    "month": 30 * 86400,
}

_eq_lock = threading.Lock()
_eq_cache: dict[str, tuple[float, list]] = {}   # key -> (fetched_at, quakes)
_eq_last_attempt: dict[str, float] = {}
_EQ_TTL = 5 * 60           # 5 minutes - quakes update ~1 min but 5 is plenty
_RETRY_BACKOFF = 120       # after a failure, wait before retrying the same key


def _summary_mag_bucket(min_mag: float) -> str | None:
    """Return the summary-feed magnitude bucket whose threshold is <=
    min_mag and closest to it, or None if a parameterised query is a
    better fit. We only use a summary feed when its threshold is at or
    below the requested minimum (so we don't miss events), then filter
    client-side up to min_mag."""
    # Thresholds of the numeric buckets.
    for thr in (4.5, 2.5, 1.0):
        if min_mag >= thr:
            return str(thr)
    return None  # min_mag < 1.0 -> use "all" via query for exactness


def _cache_file(key: str) -> Path:
    safe = key.replace("/", "_").replace(":", "_")
    return CACHE_DIR / f"quakes_{safe}.json"


def _load_eq_cache(key: str, ttl: float | None = None) -> list | None:
    path = _cache_file(key)
    eff_ttl = _EQ_TTL if ttl is None else ttl
    try:
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > eff_ttl:
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_eq_cache(key: str, quakes: list) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _cache_file(key)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(quakes, f)
        os.replace(tmp, path)
    except Exception:
        pass


def _parse_features(features: list, min_mag: float) -> list:
    """Normalise USGS GeoJSON features into simple dict rows.

    GeoJSON coordinate order is [lon, lat, depth_km]; time/updated are
    epoch MILLISECONDS. mag can be null (quarry blasts, brand-new
    automatic solutions) - those are skipped."""
    out = []
    for feat in features:
        try:
            props = feat.get("properties") or {}
            geom = feat.get("geometry") or {}
            coords = geom.get("coordinates") or []
            if len(coords) < 2:
                continue
            mag = props.get("mag")
            if mag is None or mag < min_mag:
                continue
            lon, lat = float(coords[0]), float(coords[1])
            depth = float(coords[2]) if len(coords) > 2 and coords[2] is not None else None
            out.append({
                "id": feat.get("id"),
                "lon": lon,
                "lat": lat,
                "depth_km": depth,
                "mag": float(mag),
                "place": props.get("place") or "",
                "time_ms": props.get("time"),
                "url": props.get("url") or "",
            })
        except Exception:
            continue
    # Strongest first, so bigger quakes draw on top.
    out.sort(key=lambda q: q["mag"], reverse=True)
    return out


def get_earthquakes(min_magnitude: float, period: str,
                    force: bool = False, ttl: float | None = None) -> list:
    """Return a list of earthquake dicts for the given minimum magnitude
    and time window ('hour'|'day'|'week'|'month').

    `ttl` (seconds) controls how long cached data is reused before a
    fresh network fetch - pass the user's chosen scan interval here. When
    None, falls back to the module default. Between scans the data is
    served from the on-disk cache file, so nothing is re-downloaded and
    resources are saved; only after `ttl` elapses does the cache file get
    refreshed from the network.

    Cheap and safe to call from a render thread: served from cache when
    fresh, network fetch otherwise, empty list on any failure. Never
    raises."""
    period = period if period in _SUMMARY_PERIODS else "day"
    min_magnitude = max(0.0, float(min_magnitude))
    key = f"{min_magnitude:.1f}_{period}"
    eff_ttl = _EQ_TTL if ttl is None else max(0.0, float(ttl))

    with _eq_lock:
        # In-memory fresh?
        cached = _eq_cache.get(key)
        now = time.time()
        if cached and not force and now - cached[0] < eff_ttl:
            return cached[1]
        # Disk fresh?
        disk = _load_eq_cache(key, eff_ttl) if not force else None
        if disk is not None:
            _eq_cache[key] = (now, disk)
            return disk
        # Back off after a recent failure.
        last = _eq_last_attempt.get(key, 0.0)
        if not force and now - last < _RETRY_BACKOFF:
            return cached[1] if cached else []
        _eq_last_attempt[key] = now

        features = _fetch_eq_features(min_magnitude, period)
        if features is None:
            # Fetch failed - serve stale in-memory copy if we have one.
            return cached[1] if cached else []
        quakes = _parse_features(features, min_magnitude)
        _eq_cache[key] = (now, quakes)
        _save_eq_cache(key, quakes)
        return quakes


def _fetch_eq_features(min_mag: float, period: str) -> list | None:
    """Fetch raw features from USGS, preferring the CDN summary feed when
    the bucket fits, else the FDSN query. Returns None on failure."""
    bucket = _summary_mag_bucket(min_mag)
    try:
        if bucket is not None:
            url = USGS_SUMMARY.format(mag=bucket, period=period)
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            return (r.json() or {}).get("features") or []
        # Arbitrary/very-low magnitude - parameterised query.
        seconds = PERIOD_SECONDS.get(period, 86400)
        start = datetime.fromtimestamp(time.time() - seconds, tz=timezone.utc)
        params = {
            "format": "geojson",
            "starttime": start.strftime("%Y-%m-%dT%H:%M:%S"),
            "minmagnitude": f"{min_mag:.2f}",
            "orderby": "magnitude",
            "limit": "2000",
        }
        r = requests.get(USGS_QUERY, params=params, timeout=20)
        r.raise_for_status()
        return (r.json() or {}).get("features") or []
    except Exception:
        return None


# ---- Hurricanes / tropical cyclones -------------------------------------

NHC_CURRENT = "https://www.nhc.noaa.gov/CurrentStorms.json"

_hur_lock = threading.Lock()
_hur_cache: tuple[float, list] | None = None
_hur_last_attempt: float = 0.0
_HUR_TTL = 30 * 60         # NHC advisories update every 3-6h; 30 min is fine


def _hur_cache_file() -> Path:
    return CACHE_DIR / "hurricanes.json"


def _load_hur_cache(ttl: float | None = None) -> list | None:
    path = _hur_cache_file()
    eff_ttl = _HUR_TTL if ttl is None else ttl
    try:
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > eff_ttl:
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_hur_cache(storms: list) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _hur_cache_file()
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(storms, f)
        os.replace(tmp, path)
    except Exception:
        pass


# Saffir-Simpson-ish category from max sustained wind in knots. NHC uses
# TD (<34kt), TS (34-63), then Cat 1-5. We map to a simple label + a
# rank used for marker sizing/colour.
def category_from_wind_kt(wind_kt: float | None) -> tuple[str, int]:
    if wind_kt is None:
        return "?", 0
    w = wind_kt
    if w < 34:
        return "TD", 0          # tropical depression
    if w < 64:
        return "TS", 1          # tropical storm
    if w < 83:
        return "C1", 2
    if w < 96:
        return "C2", 3
    if w < 113:
        return "C3", 4
    if w < 137:
        return "C4", 5
    return "C5", 6


def _to_float(v) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_storms(data: dict) -> list:
    """Normalise the CurrentStorms.json payload into simple storm dicts.

    The feed's exact shape has shifted over the years, so we're liberal
    about field names: we look for a list under 'activeStorms' (current)
    or fall back to the top-level list, and pull lat/lon/intensity from
    whatever keys are present."""
    storms_in = []
    if isinstance(data, dict):
        storms_in = data.get("activeStorms") or data.get("storms") or []
    elif isinstance(data, list):
        storms_in = data

    out = []
    for s in storms_in:
        try:
            if not isinstance(s, dict):
                continue
            lat = _to_float(s.get("latitudeNumeric"))
            if lat is None:
                lat = _parse_coord(s.get("latitude"))
            lon = _to_float(s.get("longitudeNumeric"))
            if lon is None:
                lon = _parse_coord(s.get("longitude"))
            if lat is None or lon is None:
                continue
            wind = _to_float(s.get("intensity")) or _to_float(s.get("intensityKt"))
            cat_label, cat_rank = category_from_wind_kt(wind)
            out.append({
                "id": s.get("id") or s.get("binNumber") or s.get("stormId"),
                "name": s.get("name") or s.get("tcName") or "Unnamed",
                "lat": lat,
                "lon": lon,
                "wind_kt": wind,
                "category": cat_label,
                "cat_rank": cat_rank,
                "classification": s.get("classification") or "",
                "basin": s.get("basin") or "",
                "movement": s.get("movement") or "",
                "pressure_mb": _to_float(s.get("pressure")),
                # Best-track / forecast geometry URLs if the feed provides
                # them - used to draw the storm's path when available.
                "track_url": _find_track_url(s),
            })
        except Exception:
            continue
    # Strongest first.
    out.sort(key=lambda s: s["cat_rank"], reverse=True)
    return out


def _parse_coord(v) -> float | None:
    """Parse an NHC coordinate like '23.4N' / '81.2W' into a signed float."""
    if v is None:
        return None
    try:
        s = str(v).strip().upper()
        if not s:
            return None
        sign = 1.0
        if s[-1] in "SW":
            sign = -1.0
            s = s[:-1]
        elif s[-1] in "NE":
            s = s[:-1]
        return sign * float(s)
    except (ValueError, IndexError):
        return None


def _find_track_url(storm: dict) -> str | None:
    """Look for a GeoJSON track/forecast URL in the storm record. NHC
    nests these under a few different keys depending on product; we try
    the common ones and accept the first that looks like GeoJSON."""
    candidates = []
    for key in ("track", "forecastTrack", "trackCone", "geojson"):
        v = storm.get(key)
        if isinstance(v, str):
            candidates.append(v)
        elif isinstance(v, dict):
            for vv in v.values():
                if isinstance(vv, str):
                    candidates.append(vv)
    for c in candidates:
        if c.lower().endswith(".geojson") or "geojson" in c.lower():
            return c
    return None


def get_hurricanes(force: bool = False, ttl: float | None = None) -> list:
    """Return a list of active tropical cyclone dicts. Cheap/safe from a
    render thread; empty list on any failure; never raises.

    `ttl` (seconds) sets how long cached storm data is reused before a
    fresh fetch - pass the user's scan interval. Between scans data comes
    from the on-disk cache, saving repeated downloads."""
    global _hur_cache, _hur_last_attempt
    eff_ttl = _HUR_TTL if ttl is None else max(0.0, float(ttl))
    with _hur_lock:
        now = time.time()
        if _hur_cache and not force and now - _hur_cache[0] < eff_ttl:
            return _hur_cache[1]
        disk = _load_hur_cache(eff_ttl) if not force else None
        if disk is not None:
            _hur_cache = (now, disk)
            return disk
        if not force and now - _hur_last_attempt < _RETRY_BACKOFF:
            return _hur_cache[1] if _hur_cache else []
        _hur_last_attempt = now
        try:
            r = requests.get(NHC_CURRENT, timeout=15,
                             headers={"User-Agent": "EarthWall/1.0"})
            r.raise_for_status()
            storms = _parse_storms(r.json())
        except Exception:
            return _hur_cache[1] if _hur_cache else []
        _hur_cache = (now, storms)
        _save_hur_cache(storms)
        return storms


def fetch_track_points(track_url: str) -> list:
    """Fetch a storm's track GeoJSON and return a list of (lon, lat)
    points for the line. Best-effort; empty on failure."""
    try:
        r = requests.get(track_url, timeout=15,
                         headers={"User-Agent": "EarthWall/1.0"})
        r.raise_for_status()
        data = r.json()
        pts: list[tuple[float, float]] = []
        for feat in (data.get("features") or []):
            geom = feat.get("geometry") or {}
            gtype = geom.get("type")
            coords = geom.get("coordinates") or []
            if gtype == "LineString":
                for c in coords:
                    if len(c) >= 2:
                        pts.append((float(c[0]), float(c[1])))
            elif gtype == "Point" and len(coords) >= 2:
                pts.append((float(coords[0]), float(coords[1])))
        return pts
    except Exception:
        return []
