"""
Optional live cloud overlay. Fetches a near-real-time global cloud map
(updated every 3 hours) from a free public service that composites
satellite data - see https://github.com/matteason/live-cloud-maps

Robustness rules, learned the hard way:

1. Once we've EVER had a cloud layer, never render without one - a failed
   refresh returns the last known good layer instead of None. Clouds
   silently blinking in and out between wallpaper updates (because one
   fetch happened to time out) looks like a bug to the user, and it was.
2. The preview worker and the full-resolution worker run on separate
   threads and can both want clouds at the same moment. All fetch/cache
   work is serialized behind a lock so we never download twice
   concurrently or, worse, interleave two writes into the cache file.
3. The disk cache is written atomically (temp file + os.replace), so a
   crash or a concurrent reader can never observe a half-written PNG.
"""
from __future__ import annotations

import os
import threading
import time
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image

CLOUD_URL = "https://clouds.matteason.co.uk/images/4096x2048/clouds-alpha.png"
CACHE_PATH = Path.home() / ".cache" / "earthwall" / "clouds-alpha.png"
CACHE_MAX_AGE = 3 * 60 * 60  # the source updates every 3 hours, matches that

_lock = threading.Lock()
_last_good: Image.Image | None = None
_last_attempt: float = 0.0
_RETRY_BACKOFF = 120  # after a failed fetch, don't hammer the server again for 2 min


def _load_cache() -> Image.Image | None:
    try:
        return Image.open(CACHE_PATH).convert("RGBA")
    except Exception:
        return None


def _save_cache_atomic(raw_bytes: bytes) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_name(CACHE_PATH.name + ".tmp")
    tmp.write_bytes(raw_bytes)
    os.replace(tmp, CACHE_PATH)


def get_cloud_layer(timeout: float = 6.0) -> Image.Image | None:
    """Return an RGBA cloud layer (white clouds, transparent elsewhere).

    Returns the freshest layer available, falling back through: fresh disk
    cache -> newly fetched -> in-memory last-known-good -> stale disk
    cache -> None (only ever None if clouds have never been available).
    """
    global _last_good, _last_attempt

    with _lock:
        # Fresh disk cache? Use it (also covers first call after restart).
        if CACHE_PATH.exists():
            age = time.time() - CACHE_PATH.stat().st_mtime
            if age < CACHE_MAX_AGE:
                cached = _load_cache()
                if cached is not None:
                    _last_good = cached
                    return _last_good

        # Cache is stale/missing. Try a refresh, unless we failed very
        # recently (avoids a 6-second timeout stalling every render while
        # offline - fail fast and reuse what we have).
        now = time.time()
        if now - _last_attempt >= _RETRY_BACKOFF:
            _last_attempt = now
            try:
                resp = requests.get(CLOUD_URL, timeout=timeout)
                resp.raise_for_status()
                img = Image.open(BytesIO(resp.content)).convert("RGBA")
                _save_cache_atomic(resp.content)
                _last_good = img
                return _last_good
            except Exception:
                pass  # fall through to the fallbacks below

        # Refresh failed or skipped: last known good beats nothing.
        if _last_good is not None:
            return _last_good

        # Cold start with no network: even a stale cache is better than
        # clouds flickering off.
        stale = _load_cache()
        if stale is not None:
            _last_good = stale
        return _last_good
