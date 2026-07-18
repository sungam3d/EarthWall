"""
On-demand downloader for extra Earth map sets from NASA.

The app ships with just two Blue Marble months to keep the download small.
This module lets users pull additional NASA imagery into their local map
library only if they want it - so the tool stays lean by default but can
grow a rich set of maps with a couple of clicks.

Why these particular images
---------------------------
Everything here is from NASA's Earth Observatory image server
(eoimages.gsfc.nasa.gov), which hosts the Blue Marble: Next Generation
monthly composites plus the "Black Marble" night-lights and a few other
global equirectangular textures. These are:

  * Public domain (NASA imagery is not copyrighted; only attribution is
    requested), so redistributing them via download links is fine.
  * Served at stable, predictable URLs that have been up for years.
  * Available in a 5400x2700 "preview" size that is a true 2:1
    equirectangular projection - sharp enough for any 4K display but only
    ~2-3 MB each, versus the 21600x10800 originals at ~25 MB+.

Each catalog entry is a day image plus (optionally) a matching night
image. When a night image isn't specific to that set (e.g. the seasonal
Blue Marble months all share the same city-lights layer), we reuse the
bundled default night map, exactly like a user-imported day-only map.

Downloading is done with urllib (no extra dependency). Files are streamed
to a temp path and only moved into place on success, so an interrupted
download never leaves a half-written map in the library.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import urllib.request
import urllib.error
from pathlib import Path

from . import maps as maps_module

# Where downloaded maps live - same dir the GUI's "import map" writes to,
# so downloaded and imported maps sit side by side and list together.
DOWNLOAD_DIR = maps_module.USER_MAPS_DIR

# NASA Earth Observatory image host. Stable for 15+ years.
_EO = "https://eoimages.gsfc.nasa.gov/images/imagerecords"

# NASA Scientific Visualization Studio hosts all 12 Blue Marble monthly
# composites in ONE directory with clean, sequential filenames - far more
# reliable than the per-month Earth Observatory "image record" IDs (which
# differ per month and are easy to get wrong). Verified present:
#   world.topo.2004-01.png ... world.topo.2004-12.png
# at 5400x2700 (true 2:1 equirectangular), ~8-9 MB PNG each. We convert to
# JPEG on download to cut disk use roughly 4x with no visible loss.
_SVS_BMNG_DIR = (
    "https://svs.gsfc.nasa.gov/vis/a010000/a012500/a012564/"
    "frames/5400x2700_2x1_60p"
)


def _bmng_url(month: str) -> str:
    return f"{_SVS_BMNG_DIR}/world.topo.2004-{month}.png"


_MONTH_NAMES = {
    "01": "January", "02": "February", "03": "March", "04": "April",
    "05": "May", "06": "June", "07": "July", "08": "August",
    "09": "September", "10": "October", "11": "November", "12": "December",
}


# The Black Marble 2012 global night lights (VIIRS). One image, reused as
# a night layer or usable as a standalone "always night" day map.
_BLACK_MARBLE = (
    f"{_EO}/79000/79765/dnb_land_ocean_ice.2012.3600x1800.jpg"
)


def _catalog() -> list[dict]:
    """Build the catalog of downloadable map sets. Each entry:
        id           stable local map_id once downloaded
        name         display name
        category     grouping for the UI
        day_url      where to fetch the day image
        night_url    optional matching night image (None = use default)
        approx_mb    rough download size, for the UI
        credit       attribution string
    """
    items: list[dict] = []
    for month in sorted(_MONTH_NAMES):
        items.append({
            "id": f"bmng_{month}",
            "name": f"Blue Marble - {_MONTH_NAMES[month]}",
            "category": "Blue Marble: Next Generation (monthly, 2004)",
            "day_url": _bmng_url(month),
            "night_url": None,   # seasonal months share the default night map
            "approx_mb": 9,      # 5400x2700 PNG from SVS
            "credit": "NASA Earth Observatory / SVS (Reto Stockli)",
        })
    # Standalone night-lights map.
    items.append({
        "id": "black_marble_2012",
        "name": "Black Marble - Night Lights (2012)",
        "category": "Night lights",
        "day_url": _BLACK_MARBLE,
        "night_url": None,
        "approx_mb": 1,
        "credit": "NASA Earth Observatory / NOAA (Suomi NPP / VIIRS)",
        "is_night_map": True,   # hint: this IS a night image
    })
    return items


def available_downloads() -> list[dict]:
    """Catalog entries that AREN'T already in the local library, so the UI
    only offers maps the user doesn't have yet."""
    have = set(maps_module.list_map_sets().keys())
    return [item for item in _catalog() if item["id"] not in have]


def all_catalog() -> list[dict]:
    """The full catalog regardless of what's installed (for showing
    already-downloaded entries greyed out, etc.)."""
    return _catalog()


class MapDownloadError(Exception):
    pass


def _download_to(url: str, dest: Path, progress=None) -> None:
    """Stream `url` to `dest`, calling progress(bytes_done, bytes_total)
    if given. Raises MapDownloadError on any network/HTTP failure."""
    req = urllib.request.Request(url, headers={
        # NASA's server rejects the default urllib UA on some paths.
        "User-Agent": "EarthWall/1.0 (+https://github.com/sungam3d/EarthWall)",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            done = 0
            chunk = 64 * 1024
            with open(dest, "wb") as f:
                while True:
                    buf = resp.read(chunk)
                    if not buf:
                        break
                    f.write(buf)
                    done += len(buf)
                    if progress:
                        progress(done, total)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as e:
        raise MapDownloadError(
            f"Couldn't download from NASA ({url.rsplit('/', 1)[-1]}): {e}"
        ) from e


def download_map_set(item: dict, progress=None) -> str:
    """Download a catalog entry into the local map library and return its
    map_id. `progress(fraction_0_to_1, message)` is called as it goes if
    given.

    The download goes to a temp directory first; only once every file is
    present do we move it into the library, so a failed or cancelled
    download can't leave a broken map behind.
    """
    map_id = item["id"]
    final_dir = DOWNLOAD_DIR / map_id
    if final_dir.exists():
        raise MapDownloadError(f"'{item['name']}' is already downloaded.")

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix="earthwall_dl_"))
    try:
        # --- day image ---
        if progress:
            progress(0.0, f"Downloading {item['name']}…")
        day_tmp = tmp_dir / "day.jpg"

        def _day_prog(done, total):
            if progress and total:
                # Day image is the bulk; give it 0-0.8 of the bar (night,
                # if any, gets 0.8-1.0).
                progress(min(0.8, done / total * 0.8), f"Downloading {item['name']}…")

        _download_to(item["day_url"], day_tmp, _day_prog)

        # --- optional night image ---
        has_night = bool(item.get("night_url"))
        if has_night:
            night_tmp = tmp_dir / "night.jpg"

            def _night_prog(done, total):
                if progress and total:
                    progress(0.8 + min(0.2, done / total * 0.2),
                             f"Downloading {item['name']} (night)…")

            _download_to(item["night_url"], night_tmp, _night_prog)

        # --- validate + normalise the day image. We re-encode to JPEG
        #     regardless of source format: NASA's SVS monthlies are 8-9 MB
        #     PNGs, and re-saving as quality-92 JPEG cuts that to ~2-3 MB
        #     with no visible difference at wallpaper scale. This also
        #     catches HTML error pages / truncated files (they won't
        #     decode). ---
        if progress:
            progress(0.9, "Verifying and converting image…")
        try:
            from PIL import Image
            with Image.open(day_tmp) as im:
                im_rgb = im.convert("RGB")
                day_jpg = tmp_dir / "day_conv.jpg"
                im_rgb.save(day_jpg, "JPEG", quality=92)
        except Exception as e:
            raise MapDownloadError(
                f"The downloaded file for '{item['name']}' isn't a valid "
                f"image - the source may have moved. ({e})"
            ) from e
        night_jpg = None
        if has_night:
            try:
                from PIL import Image
                with Image.open(night_tmp) as im:
                    night_jpg = tmp_dir / "night_conv.jpg"
                    im.convert("RGB").save(night_jpg, "JPEG", quality=92)
            except Exception as e:
                raise MapDownloadError(
                    f"The night image for '{item['name']}' isn't valid. ({e})"
                ) from e

        # --- move into place atomically-ish ---
        final_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(day_jpg), str(final_dir / "day.jpg"))
        if has_night and night_jpg is not None:
            shutil.move(str(night_jpg), str(final_dir / "night.jpg"))
        with open(final_dir / "meta.json", "w") as f:
            json.dump({
                "name": item["name"],
                "builtin": False,
                "downloaded": True,
                "credit": item.get("credit", "NASA"),
            }, f)

        if progress:
            progress(1.0, f"{item['name']} ready.")
        return map_id
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
