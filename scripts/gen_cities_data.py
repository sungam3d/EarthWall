"""Build script: read the geonamescache dataset and emit a compact
JSON file with the ~32k cities we want to ship. Run once from a dev
machine that has geonamescache installed; commit the resulting
earthwall/data/cities.json.gz to source control. Runtime doesn't need
geonamescache at all - it just reads the compressed JSON.

Format is a plain list of records to keep the loader tiny:
    [name, country_name, admin1_code, lat, lon, tz]

Fields:
    name           display name (e.g. "Lake Jackson")
    country_name   English country name (e.g. "United States")
    admin1_code    ISO admin1 code (e.g. "TX") or "" when we don't need
                   to disambiguate. Only set for countries where we ship
                   a code-to-name mapping (US, CA, AU, BR, MX, IN, DE).
    lat, lon       floats
    tz             IANA timezone (e.g. "America/Chicago")

We KEEP admin1 for every city in the covered countries so the search
label can show "Lake Jackson, Texas, United States" without ambiguity
with any other Lake Jackson. For other countries the admin1 is left
blank to save bytes.
"""
from __future__ import annotations
import gzip
import json
from pathlib import Path

import geonamescache

# Countries where a state / province code meaningfully disambiguates
# same-named cities. For anywhere not in this set the admin code is
# dropped (saves ~150KB in the compressed output).
_ADMIN_COUNTRIES = {"US", "CA", "AU", "BR", "MX", "IN", "DE", "GB", "CN"}


def main() -> None:
    gc = geonamescache.GeonamesCache()
    countries = gc.get_countries()
    cities = gc.get_cities()

    out = []
    for c in cities.values():
        cc = c["countrycode"]
        country_name = countries.get(cc, {}).get("name", cc)
        admin = c.get("admin1code", "") or ""
        if cc not in _ADMIN_COUNTRIES:
            admin = ""
        out.append([
            c["name"],
            country_name,
            admin,
            round(float(c["latitude"]), 4),
            round(float(c["longitude"]), 4),
            c["timezone"] or "UTC",
        ])
    # Sort by name (case-insensitive) then country - deterministic output
    # so the compressed file is stable across regenerations, which keeps
    # git diffs meaningful when we bump geonamescache versions.
    out.sort(key=lambda r: (r[0].lower(), r[1]))

    data_dir = Path(__file__).resolve().parent.parent / "earthwall" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    dest = data_dir / "cities.json.gz"

    payload = json.dumps({"schema": 1, "cities": out},
                          separators=(",", ":"), ensure_ascii=False)
    with gzip.open(dest, "wt", encoding="utf-8", compresslevel=9) as f:
        f.write(payload)

    raw_kb = len(payload.encode("utf-8")) // 1024
    gz_kb = dest.stat().st_size // 1024
    print(f"wrote {dest}")
    print(f"  cities: {len(out)}")
    print(f"  raw JSON: {raw_kb} KB, gzipped: {gz_kb} KB")


if __name__ == "__main__":
    main()
