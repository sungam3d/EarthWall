"""
Backing store for the "add city" search UI.

Ships with ~32,000 cities from the geonamescache dataset (world cities
with population >= ~15k). That covers everything from world capitals
down to mid-size towns like Lake Jackson, Texas. For places smaller
than that the GUI still allows fully-manual entry (name/lat/lon/tz).

The city list itself is a shipped gzipped-JSON data file at
`earthwall/data/cities.json.gz` (~580 KB), loaded lazily on first
search so importing this module stays cheap. Old code that iterated
`CITY_DATABASE` still works via a small sequence proxy.

Search is case- and accent-insensitive, ranked (best matches first),
and shows "City, Admin, Country" so identically-named places (many US
Springfields, several Victorias, etc.) can be told apart.

Row format:
    (name, country_name, admin_code, lat, lon, tz)
where `admin_code` is a state/province code like "TX" or "" if we
don't ship an admin code for the country.
"""
from __future__ import annotations

import gzip
import json
import unicodedata
from functools import lru_cache
from pathlib import Path


_DATA_PATH = Path(__file__).resolve().parent / "data" / "cities.json.gz"


# Full names for admin codes we ship. US states are the ones we ship
# names for since American users tend to refer to them by name ("Lake
# Jackson, Texas"), whereas most other countries' subdivisions are
# communicated by code or omitted entirely. Adding entries for other
# countries here is safe - anything missing simply falls back to the
# raw admin code in the display label.
_ADMIN_NAMES = {
    ("US", "AL"): "Alabama",       ("US", "AK"): "Alaska",
    ("US", "AZ"): "Arizona",       ("US", "AR"): "Arkansas",
    ("US", "CA"): "California",    ("US", "CO"): "Colorado",
    ("US", "CT"): "Connecticut",   ("US", "DE"): "Delaware",
    ("US", "FL"): "Florida",       ("US", "GA"): "Georgia",
    ("US", "HI"): "Hawaii",        ("US", "ID"): "Idaho",
    ("US", "IL"): "Illinois",      ("US", "IN"): "Indiana",
    ("US", "IA"): "Iowa",          ("US", "KS"): "Kansas",
    ("US", "KY"): "Kentucky",      ("US", "LA"): "Louisiana",
    ("US", "ME"): "Maine",         ("US", "MD"): "Maryland",
    ("US", "MA"): "Massachusetts", ("US", "MI"): "Michigan",
    ("US", "MN"): "Minnesota",     ("US", "MS"): "Mississippi",
    ("US", "MO"): "Missouri",      ("US", "MT"): "Montana",
    ("US", "NE"): "Nebraska",      ("US", "NV"): "Nevada",
    ("US", "NH"): "New Hampshire", ("US", "NJ"): "New Jersey",
    ("US", "NM"): "New Mexico",    ("US", "NY"): "New York",
    ("US", "NC"): "North Carolina",("US", "ND"): "North Dakota",
    ("US", "OH"): "Ohio",          ("US", "OK"): "Oklahoma",
    ("US", "OR"): "Oregon",        ("US", "PA"): "Pennsylvania",
    ("US", "RI"): "Rhode Island",  ("US", "SC"): "South Carolina",
    ("US", "SD"): "South Dakota",  ("US", "TN"): "Tennessee",
    ("US", "TX"): "Texas",         ("US", "UT"): "Utah",
    ("US", "VT"): "Vermont",       ("US", "VA"): "Virginia",
    ("US", "WA"): "Washington",    ("US", "WV"): "West Virginia",
    ("US", "WI"): "Wisconsin",     ("US", "WY"): "Wyoming",
    ("US", "DC"): "Washington DC",
    # Canadian provinces
    ("CA", "AB"): "Alberta",        ("CA", "BC"): "British Columbia",
    ("CA", "MB"): "Manitoba",       ("CA", "NB"): "New Brunswick",
    ("CA", "NL"): "Newfoundland",   ("CA", "NS"): "Nova Scotia",
    ("CA", "ON"): "Ontario",        ("CA", "PE"): "Prince Edward Island",
    ("CA", "QC"): "Quebec",         ("CA", "SK"): "Saskatchewan",
    ("CA", "YT"): "Yukon",          ("CA", "NT"): "Northwest Territories",
    ("CA", "NU"): "Nunavut",
    # Australian states / territories
    ("AU", "NSW"): "New South Wales", ("AU", "VIC"): "Victoria",
    ("AU", "QLD"): "Queensland",      ("AU", "SA"):  "South Australia",
    ("AU", "WA"):  "Western Australia",("AU", "TAS"): "Tasmania",
    ("AU", "NT"):  "Northern Territory",("AU", "ACT"):"Australian Capital Territory",
}


# Country code lookup for the couple of countries where we translate
# admin codes to full names.
_COUNTRY_TO_CODE = {
    "United States": "US",
    "Canada": "CA",
    "Australia": "AU",
}


def _fold(s: str) -> str:
    """Lowercase + strip diacritics so 'sao' matches 'São', 'cordoba'
    matches 'Córdoba', 'zurich' matches 'Zürich'. NFKD decomposition
    plus dropping combining marks."""
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", s.lower())
        if not unicodedata.combining(ch)
    )


@lru_cache(maxsize=1)
def _load_cities() -> list[tuple]:
    """Load and cache the shipped city dataset. Runs once per process."""
    if not _DATA_PATH.exists():
        # Development / partial install: return an empty database rather
        # than raising, so the GUI still opens and manual-entry works.
        return []
    try:
        with gzip.open(_DATA_PATH, "rt", encoding="utf-8") as f:
            blob = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    rows = blob.get("cities", [])
    return [tuple(r) for r in rows]


@lru_cache(maxsize=1)
def _load_folded_names() -> list[tuple[str, str]]:
    """Precomputed (folded_name, folded_country) pairs, in the same
    index order as _load_cities(). Cached because folding 32k strings
    on every keystroke would be visible."""
    return [(_fold(c[0]), _fold(c[1])) for c in _load_cities()]


class _LazyCityList:
    """Backwards-compatible sequence proxy for old code that iterated
    or indexed CITY_DATABASE directly. Loads on first access."""
    def __iter__(self):
        return iter(_load_cities())
    def __len__(self):
        return len(_load_cities())
    def __getitem__(self, i):
        return _load_cities()[i]
    def __bool__(self):
        return bool(_load_cities())


CITY_DATABASE = _LazyCityList()


def city_label(city: tuple) -> str:
    """Human-readable label for a database row. Shape is:
        "City, Admin, Country"    when we have an admin code
        "City, Country"           otherwise
    Admin is shown as its full name for US/CA/AU (Texas, Ontario, ...)
    and as the raw code for other admin countries (São Paulo → SP).
    """
    name = city[0]
    country = city[1]
    admin = city[2] if len(city) > 2 else ""
    if not admin:
        return f"{name}, {country}"
    cc = _COUNTRY_TO_CODE.get(country)
    if cc:
        full = _ADMIN_NAMES.get((cc, admin))
        if full:
            return f"{name}, {full}, {country}"
    return f"{name}, {admin}, {country}"


def search_cities(query: str, limit: int = 40) -> list[tuple]:
    """Case- and accent-insensitive search over city, admin, and country
    names. Ranking (best first):
      1. City name equals the query
      2. City name starts with the query
      3. City name contains the query
      4. Admin or country contains the query
    Ties are broken by keeping the load-time (alphabetical) order.
    """
    q = _fold(query.strip())
    if not q:
        return _load_cities()[:limit]

    exact, starts, contains, other = [], [], [], []
    for c, (fn, fc) in zip(_load_cities(), _load_folded_names()):
        if fn == q:
            exact.append(c)
        elif fn.startswith(q):
            starts.append(c)
        elif q in fn:
            contains.append(c)
        elif q in fc or (len(c) > 2 and c[2] and q in _fold(c[2])):
            other.append(c)
    return (exact + starts + contains + other)[:limit]


def find_city(label_or_name: str) -> tuple | None:
    """Resolve a display label (or a bare city name) back to a database
    row. Prefers an exact label match, then falls back to first city
    with a matching (accent-folded) name."""
    s = label_or_name.strip()
    if not s:
        return None
    for c in _load_cities():
        if city_label(c) == s:
            return c
    low = _fold(s)
    for c, (fn, _fc) in zip(_load_cities(), _load_folded_names()):
        if fn == low:
            return c
    return None


def all_labels() -> list[str]:
    """All city labels in the database, for building completer models."""
    return [city_label(c) for c in _load_cities()]
