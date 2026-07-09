"""
Optional live cloud overlay. Fetches a near-real-time global cloud map
(updated every 3 hours) from a free public service that composites
satellite data - see https://github.com/matteason/live-cloud-maps

The Earth imagery underneath is NASA (public domain); the cloud data
itself is EUMETSAT satellite data via that project, released CC0. If the
fetch fails for any reason (no internet, service down), we fail quietly
and the render just proceeds without clouds - this is a nice-to-have
layer, never something that should break the wallpaper.
"""
from __future__ import annotations

import time
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image

CLOUD_URL = "https://clouds.matteason.co.uk/images/4096x2048/clouds-alpha.png"
CACHE_PATH = Path.home() / ".cache" / "earthwall" / "clouds-alpha.png"
CACHE_MAX_AGE = 3 * 60 * 60  # the source updates every 3 hours, matches that


def get_cloud_layer(timeout: float = 6.0) -> Image.Image | None:
    """Return an RGBA cloud layer image (white clouds, transparent elsewhere),
    or None if it couldn't be fetched and there's no usable cache."""
    if CACHE_PATH.exists():
        age = time.time() - CACHE_PATH.stat().st_mtime
        if age < CACHE_MAX_AGE:
            try:
                return Image.open(CACHE_PATH).convert("RGBA")
            except Exception:
                pass

    try:
        resp = requests.get(CLOUD_URL, timeout=timeout)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content)).convert("RGBA")
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        img.save(CACHE_PATH)
        return img
    except Exception:
        # Fall back to a stale cache rather than nothing, if one exists.
        if CACHE_PATH.exists():
            try:
                return Image.open(CACHE_PATH).convert("RGBA")
            except Exception:
                pass
        return None
