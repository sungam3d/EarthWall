"""
Manages "map sets" (a day image + a night/city-lights image, treated as
a pair). Handles both the bundled built-in maps and user-imported ones.

Importing is deliberately permissive: Pillow can open essentially every
common image format (JPEG, PNG, BMP, TIFF, WEBP, GIF, and more) regardless
of the file extension, so "decoding" a user's own map image is mostly a
matter of just opening it and converting to RGB. We validate the aspect
ratio loosely (equirectangular maps are 2:1) and warn rather than reject,
since a slightly-off image will still render, just a bit stretched.
"""
from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from PIL import Image, UnidentifiedImageError

BUILTIN_MAPS_DIR = Path(__file__).resolve().parent.parent / "assets" / "maps"
DEFAULT_NIGHT_MAP = Path(__file__).resolve().parent.parent / "assets" / "default_night" / "night.jpg"
USER_MAPS_DIR = Path.home() / ".config" / "earthwall" / "maps"

# Imported maps get downscaled to this max width to keep render time and
# disk usage sane - still comfortably sharper than any 4K display needs.
IMPORT_MAX_WIDTH = 5400


class MapImportError(Exception):
    pass


def _read_meta(map_dir: Path) -> dict:
    meta_path = map_dir / "meta.json"
    if meta_path.exists():
        try:
            with open(meta_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"name": map_dir.name, "builtin": False}


def list_map_sets() -> dict[str, dict]:
    """Return {map_id: {name, day_path, night_path, builtin, has_custom_night}}."""
    result = {}
    for base_dir, is_builtin in [(BUILTIN_MAPS_DIR, True), (USER_MAPS_DIR, False)]:
        if not base_dir.exists():
            continue
        for map_dir in sorted(base_dir.iterdir()):
            if not map_dir.is_dir():
                continue
            day_path = map_dir / "day.jpg"
            if not day_path.exists():
                day_path = map_dir / "day.png"
            night_path = map_dir / "night.jpg"
            if not night_path.exists():
                night_path = map_dir / "night.png"
            if not day_path.exists():
                continue
            meta = _read_meta(map_dir)
            result[map_dir.name] = {
                "name": meta.get("name", map_dir.name),
                "day_path": day_path,
                "night_path": night_path if night_path.exists() else DEFAULT_NIGHT_MAP,
                "builtin": is_builtin,
                "has_custom_night": night_path.exists(),
            }
    return result


def _decode_and_prepare(src_path: str | Path) -> Image.Image:
    """Open any common image format and normalize it to an RGB equirectangular
    map, raising a friendly error if the file isn't a readable image."""
    try:
        img = Image.open(src_path)
        img.load()
    except (UnidentifiedImageError, OSError) as e:
        raise MapImportError(
            f"Couldn't read '{Path(src_path).name}' as an image. "
            f"Supported formats: JPEG, PNG, BMP, TIFF, WEBP, GIF."
        ) from e

    img = img.convert("RGB")

    w, h = img.size
    if w > IMPORT_MAX_WIDTH:
        new_h = int(h * (IMPORT_MAX_WIDTH / w))
        img = img.resize((IMPORT_MAX_WIDTH, new_h), Image.LANCZOS)

    return img


def check_aspect_ratio(src_path: str | Path) -> bool:
    """Returns True if the image is close to the standard 2:1 equirectangular
    aspect ratio. Used by the GUI to show a gentle warning, not to block import."""
    try:
        with Image.open(src_path) as img:
            w, h = img.size
        return 1.8 <= (w / h) <= 2.2
    except Exception:
        return True  # don't block import just because we couldn't check


def import_map_set(display_name: str, day_image_path: str | Path,
                    night_image_path: str | Path | None = None) -> str:
    """Import a user-supplied day map (and optional night map) as a new
    named map set. Returns the new map_id.

    If no night map is given, the bundled default night-lights map is
    used instead (resized to match), so city lights still show up.
    """
    day_img = _decode_and_prepare(day_image_path)

    if night_image_path:
        night_img = _decode_and_prepare(night_image_path)
        night_img = night_img.resize(day_img.size, Image.LANCZOS)
        has_custom_night = True
    else:
        night_img = Image.open(DEFAULT_NIGHT_MAP).convert("RGB")
        night_img = night_img.resize(day_img.size, Image.LANCZOS)
        has_custom_night = False

    map_id = f"custom_{uuid.uuid4().hex[:8]}"
    map_dir = USER_MAPS_DIR / map_id
    map_dir.mkdir(parents=True, exist_ok=True)

    day_img.save(map_dir / "day.jpg", quality=92)
    if has_custom_night:
        night_img.save(map_dir / "night.jpg", quality=92)

    with open(map_dir / "meta.json", "w") as f:
        json.dump({"name": display_name, "builtin": False}, f)

    return map_id


def delete_map_set(map_id: str) -> None:
    map_dir = USER_MAPS_DIR / map_id
    if not map_dir.exists() or not map_dir.is_relative_to(USER_MAPS_DIR):
        raise MapImportError("That map can't be deleted (it may be a built-in map).")
    shutil.rmtree(map_dir)
    try:
        from . import render as render_module
        render_module.invalidate_map_cache(map_id)
    except ImportError:
        pass
