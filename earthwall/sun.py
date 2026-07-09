"""
Solar position calculations.

Computes the "subsolar point" - the single point on Earth's surface where
the sun is directly overhead at a given moment. Everything about the
day/night terminator falls out of this one point using spherical geometry,
so this is the only astronomy we need.

Algorithm is the standard NOAA solar-position approximation (the same
family of formulas xplanet, most planetariums, and EarthView-style tools
use). Accurate to a small fraction of a degree - overkill precision for a
wallpaper, but it's cheap so we don't cut corners.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone


def subsolar_point(when: datetime | None = None) -> tuple[float, float]:
    """Return (latitude, longitude) in degrees of the subsolar point.

    `when` must be timezone-aware; if omitted, uses the current UTC time.
    """
    if when is None:
        when = datetime.now(timezone.utc)
    elif when.tzinfo is None:
        raise ValueError("subsolar_point requires a timezone-aware datetime")

    when_utc = when.astimezone(timezone.utc)

    # Day-of-year fraction, including time-of-day, expressed as an angle
    # around Earth's orbit (gamma). This is the standard NOAA formulation.
    start_of_year = when_utc.replace(month=1, day=1, hour=0, minute=0,
                                      second=0, microsecond=0)
    day_of_year = (when_utc - start_of_year).days + 1
    hour_frac = when_utc.hour + when_utc.minute / 60 + when_utc.second / 3600

    gamma = 2 * math.pi / 365 * (day_of_year - 1 + (hour_frac - 12) / 24)

    # Equation of time, in minutes - the difference between apparent solar
    # time and mean (clock) solar time, caused by orbital eccentricity and
    # axial tilt.
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )

    # Solar declination, in radians - this directly becomes the subsolar
    # latitude (how far north/south the sun is currently overhead, i.e.
    # what drives the seasons).
    decl = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148 * math.sin(3 * gamma)
    )

    subsolar_lat = math.degrees(decl)

    # Subsolar longitude - the longitude currently experiencing true solar
    # noon.
    true_solar_time = hour_frac * 60 + eqtime  # minutes
    subsolar_lon = -15 * (true_solar_time / 60 - 12)
    subsolar_lon = ((subsolar_lon + 180) % 360) - 180  # wrap to [-180, 180]

    return subsolar_lat, subsolar_lon
