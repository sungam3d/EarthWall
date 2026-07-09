"""
Renders a single equirectangular Earth wallpaper frame: day map and night
(city-lights) map blended together along the real-time day/night
terminator, with optional labeled markers for chosen cities, an optional
live cloud layer, and support for re-centering the map on any longitude.
"""
from __future__ import annotations

import math
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .sun import subsolar_point
from . import maps as maps_module

# Decoding the source JPEGs (5400x2700 + 3600x1800) is a fixed cost on
# every render regardless of the requested output size. Since the same
# map set is typically re-rendered many times in a row (auto-update timer,
# a burst of preview renders while tweaking settings), keep the decoded
# originals in memory rather than re-reading and re-decoding from disk
# every time. Safe to keep unbounded in practice - there are only ever a
# handful of map sets, and they're a few MB each once decoded.
_MAP_CACHE: dict[str, tuple[Image.Image, Image.Image]] = {}


def _load_maps(map_id: str) -> tuple[Image.Image, Image.Image]:
    if map_id in _MAP_CACHE:
        return _MAP_CACHE[map_id]

    available = maps_module.list_map_sets()
    if map_id not in available:
        map_id = next(iter(available))
        if map_id in _MAP_CACHE:
            return _MAP_CACHE[map_id]

    entry = available[map_id]
    day = Image.open(entry["day_path"]).convert("RGB")
    night = Image.open(entry["night_path"]).convert("RGB")
    if night.size != day.size:
        night = night.resize(day.size, Image.LANCZOS)

    _MAP_CACHE[map_id] = (day, night)
    return day, night


def invalidate_map_cache(map_id: str | None = None) -> None:
    """Drop cached decoded map(s) - call after deleting a map set so a
    stale copy doesn't linger in memory needlessly."""
    if map_id is None:
        _MAP_CACHE.clear()
    else:
        _MAP_CACHE.pop(map_id, None)


def _roll_longitude(img: Image.Image, center_lon: float) -> Image.Image:
    """Shift an equirectangular image horizontally so `center_lon` sits in
    the middle of the frame, wrapping around the edges. center_lon=0 is a
    no-op (the standard Prime-Meridian-centered layout)."""
    if center_lon == 0:
        return img
    w, h = img.size
    shift_px = int(round((center_lon / 360.0) * w))
    arr = np.asarray(img)
    rolled = np.roll(arr, -shift_px, axis=1)
    return Image.fromarray(rolled)


def _day_night_mask(width: int, height: int, sub_lat: float, sub_lon: float,
                     twilight_width_deg: float) -> np.ndarray:
    # Deliberately independent of center_lon: this mask is applied to the
    # day/night source images in their original (pre-roll) pixel layout,
    # where pixel j always represents true longitude -180 + j/width*360
    # regardless of how the final image will later be re-centered. The
    # re-centering itself happens afterwards via _roll_longitude on the
    # already-correctly-blended composite - mixing the two would shift
    # the terminator to the wrong place (and it did, until this fix).
    lons = np.linspace(-180, 180, width, endpoint=False) + 180 / width
    # Pixel-center latitudes: row i spans from (90 - i*180/h) down, so its
    # center is that minus half a row height.
    lats = np.linspace(90, -90, height, endpoint=False) - 90.0 / height
    lat_grid, lon_grid = np.meshgrid(np.radians(lats), np.radians(lons), indexing="ij")

    sub_lat_r = math.radians(sub_lat)
    sub_lon_r = math.radians(sub_lon)

    cos_zenith = (
        np.sin(lat_grid) * math.sin(sub_lat_r)
        + np.cos(lat_grid) * math.cos(sub_lat_r) * np.cos(lon_grid - sub_lon_r)
    )

    half_width = math.sin(math.radians(twilight_width_deg))
    mask = np.clip((cos_zenith + half_width) / (2 * half_width), 0.0, 1.0)
    mask = mask * mask * (3 - 2 * mask)
    return mask.astype(np.float32)


def _composite_day_night(day_img: Image.Image, night_img: Image.Image,
                          sub_lat: float, sub_lon: float,
                          twilight_width_deg: float) -> Image.Image:
    w, h = day_img.size
    mask = _day_night_mask(w, h, sub_lat, sub_lon, twilight_width_deg)[:, :, None]

    day_arr = np.asarray(day_img, dtype=np.float32)
    night_arr = np.asarray(night_img, dtype=np.float32)

    dim_day = day_arr * 0.12
    night_layer = np.maximum(night_arr, dim_day)

    out = day_arr * mask + night_layer * (1 - mask)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), mode="RGB")


def _apply_clouds(base: Image.Image, cloud_layer: Image.Image, center_lon: float,
                   opacity: float) -> Image.Image:
    cloud_layer = cloud_layer.resize(base.size, Image.LANCZOS)
    cloud_layer = _roll_longitude(cloud_layer, center_lon)

    r, g, b, a = cloud_layer.split()
    a = a.point(lambda p: int(p * opacity))
    cloud_layer = Image.merge("RGBA", (r, g, b, a))

    base = base.convert("RGBA")
    return Image.alpha_composite(base, cloud_layer).convert("RGB")


def _lonlat_to_xy(lon: float, lat: float, width: int, height: int, center_lon: float) -> tuple[int, int]:
    rel_lon = ((lon - center_lon + 180) % 360) - 180
    x = (rel_lon + 180) / 360 * width
    y = (90 - lat) / 180 * height
    return int(x), int(y)


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _draw_city_markers(img: Image.Image, cities: list[dict], now_utc: datetime,
                        center_lon: float) -> Image.Image:
    draw = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    font_size = max(11, w // 220)
    font = _load_font(font_size)
    placed_boxes: list[tuple[int, int, int, int]] = []

    def _overlaps(box: tuple[int, int, int, int]) -> bool:
        for other in placed_boxes:
            if not (box[2] < other[0] or box[0] > other[2]
                    or box[3] < other[1] or box[1] > other[3]):
                return True
        return False

    for city in cities:
        lon, lat = city["lon"], city["lat"]
        x, y = _lonlat_to_xy(lon, lat, w, h, center_lon)

        try:
            local_time = now_utc.astimezone(ZoneInfo(city["tz"])).strftime("%H:%M")
        except Exception:
            local_time = "--:--"

        color = tuple(city.get("color", (255, 210, 60)))
        marker_r = max(3, font_size // 3)

        draw.ellipse(
            [x - marker_r, y - marker_r, x + marker_r, y + marker_r],
            fill=(*color, 255), outline=(20, 20, 20, 255), width=2,
        )

        label = f"{city['name']}  {local_time}"
        text_bbox = draw.textbbox((0, 0), label, font=font)
        tw, th = text_bbox[2] - text_bbox[0], text_bbox[3] - text_bbox[1]
        pad_x, pad_y = 8, 5
        box_h = th + pad_y * 2

        label_x = x + marker_r + 8
        if label_x + tw + pad_x * 2 > w:
            label_x = x - marker_r - 8 - tw - pad_x * 2
        base_y = y - th // 2 - pad_y

        # If this label would sit on top of one already drawn (clustered
        # cities like London/Paris/Amsterdam), nudge it up/down in steps
        # until it finds clear space - far more readable than a pile-up.
        label_y = base_y
        for offset_steps in range(0, 6):
            for direction in (1, -1) if offset_steps else (1,):
                candidate_y = base_y + direction * offset_steps * (box_h + 3)
                candidate = (label_x, candidate_y,
                             label_x + tw + pad_x * 2, candidate_y + box_h)
                if not _overlaps(candidate) and 0 <= candidate_y and candidate[3] <= h:
                    label_y = candidate_y
                    break
            else:
                continue
            break

        box = [label_x, label_y, label_x + tw + pad_x * 2, label_y + box_h]
        placed_boxes.append(tuple(box))
        draw.rounded_rectangle(box, radius=6, fill=(15, 15, 20, 165))
        draw.text((label_x + pad_x, label_y + pad_y - text_bbox[1]), label,
                   font=font, fill=(255, 255, 255, 255))

    return img


def render(output_path: str | Path, width: int, height: int,
           cities: list[dict], when: datetime | None = None,
           map_id: str = "blue_marble_july", center_lon: float = 0.0,
           twilight_width_deg: float = 7.0,
           cloud_layer: Image.Image | None = None,
           cloud_opacity: float = 0.35) -> None:
    """Render one wallpaper frame and save it to `output_path`."""
    when = when or datetime.now().astimezone()
    sub_lat, sub_lon = subsolar_point(when)

    day_img, night_img = _load_maps(map_id)

    # Downsample to the target size FIRST, before the per-pixel day/night
    # blend - the blend math (and the unsharp mask afterwards) then scales
    # with the requested output size instead of the ~5400x2700 source
    # resolution every time. This is the difference between a "low-res
    # preview" actually being fast versus doing full-resolution work and
    # throwing most of it away at the final resize.
    if day_img.size != (width, height):
        day_img = day_img.resize((width, height), Image.LANCZOS)
        night_img = night_img.resize((width, height), Image.LANCZOS)

    composite = _composite_day_night(day_img, night_img, sub_lat, sub_lon,
                                      twilight_width_deg)
    composite = _roll_longitude(composite, center_lon)
    composite = composite.filter(ImageFilter.UnsharpMask(radius=1.5, percent=60, threshold=2))

    if cloud_layer is not None:
        composite = _apply_clouds(composite, cloud_layer, center_lon, cloud_opacity)

    composite = _draw_city_markers(composite, cities, when.astimezone(), center_lon)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Atomic write: render to a temp file, then rename over the target.
    # The desktop environment watches/reads the target path - writing it
    # in place means it can reload a half-written file mid-save, which
    # shows up as the wallpaper blinking to black during every update.
    # os.replace() is atomic on the same filesystem, so readers only ever
    # see either the complete old image or the complete new one.
    suffix = output_path.suffix.lower()
    fmt = "JPEG" if suffix in (".jpg", ".jpeg") else "PNG"
    tmp_path = output_path.with_name(output_path.name + ".tmp")
    if fmt == "JPEG":
        composite.save(tmp_path, format=fmt, quality=90)
    else:
        composite.save(tmp_path, format=fmt)
    os.replace(tmp_path, output_path)
