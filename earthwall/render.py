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
                          twilight_width_deg: float,
                          night_darkness: float = 0.85) -> Image.Image:
    w, h = day_img.size
    mask = _day_night_mask(w, h, sub_lat, sub_lon, twilight_width_deg)[:, :, None]

    day_arr = np.asarray(day_img, dtype=np.float32)
    night_arr = np.asarray(night_img, dtype=np.float32)

    # Night side rendering:
    # - Bright city lights (from the Black Marble night map) should stay
    #   vivid so they're the visual focus of the dark side.
    # - Unlit land/ocean should look genuinely dark. The catch is that the
    #   Black Marble source itself has heavily blue-tinted oceans (mean
    #   B channel ~44 vs R ~7), so simply blending it against the day map
    #   ends up looking "just tinted blue at night" - the user's actual
    #   complaint - even though we're using the correct night map.
    # - `night_darkness` (0..1) both suppresses the day-map fallback AND
    #   scales down the dim parts of the night map itself, while
    #   preserving bright pixels (city lights) untouched. This gives the
    #   user a slider that goes from the old washed-out look to a proper
    #   deep-black night with just lights showing.
    dim_factor = max(0.0, 0.12 * (1.0 - night_darkness))
    dim_day = day_arr * dim_factor

    # Nonlinear night-map darkening: preserve bright pixels (max channel
    # value close to 255) so city lights don't dim; aggressively darken
    # mid-tones (the blue oceans) proportional to the slider. This is a
    # per-pixel brightness weight in [0, 1] where 1 = "as bright as it
    # gets, don't touch" and lower values scale the whole pixel down.
    max_chan = night_arr.max(axis=2, keepdims=True) / 255.0
    # brightness_weight rises fast toward 1 as pixels get bright, so
    # city-light pixels (max_chan ~1) keep almost their full value while
    # dim pixels (max_chan < 0.4, i.e. most ocean) get scaled way down.
    brightness_weight = max_chan ** 0.6
    dark_scale = 1.0 - night_darkness * (1.0 - brightness_weight)
    darkened_night = night_arr * dark_scale

    night_layer = np.maximum(darkened_night, dim_day)

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
    """Legacy default-font loader kept for backwards compatibility; prefer
    fonts.resolve(family, style, size) for per-field font choices."""
    from . import fonts as fonts_module
    return fonts_module.resolve(fonts_module.DEFAULT_FAMILY, "Bold", size)


def _field_style(city: dict, field: str, base_font_size: int,
                  default_color: tuple) -> dict:
    """Resolve per-field label styling. Each field (name/time/weather/notes)
    can have its own font family, style, size multiplier, and colour.
    Legacy configs without any per-field style keys fall back to the
    label_scale + text_color values used before this feature."""
    prefix = f"{field}_"
    from . import fonts as fonts_module

    family = city.get(f"{prefix}font_family",
                       city.get("font_family", fonts_module.DEFAULT_FAMILY))
    style = city.get(f"{prefix}font_style",
                       city.get("font_style", "Bold"))
    scale_default = float(city.get("label_scale", 1.0))
    scale = float(city.get(f"{prefix}font_scale", scale_default))
    color = city.get(f"{prefix}color", city.get("text_color", list(default_color)))

    size = max(8, int(base_font_size * scale))
    font = fonts_module.resolve(family, style, size)

    return {
        "font": font,
        "font_size": size,
        "color": tuple(color),
    }


def _build_label_segments(city: dict, now_utc: datetime,
                            weather_reading, temp_units: str,
                            base_font_size: int, default_color: tuple) -> list[dict]:
    """Return a list of styled segments, one per label line. Each segment is
    a dict {text, font, font_size, color}. Fields with show_* == False are
    simply skipped."""
    segments: list[dict] = []

    if city.get("show_name", True):
        style = _field_style(city, "name", base_font_size, default_color)
        style["text"] = city.get("name", "Unnamed")
        segments.append(style)

    if city.get("show_time", True):
        try:
            local_time = now_utc.astimezone(ZoneInfo(city["tz"])).strftime("%H:%M")
        except Exception:
            local_time = "--:--"
        style = _field_style(city, "time", base_font_size, default_color)
        style["text"] = local_time
        segments.append(style)

    if city.get("show_weather", False) and weather_reading is not None:
        w_line = f"{weather_reading.emoji}  {weather_reading.temp_display(temp_units)}".strip()
        if weather_reading.label and weather_reading.label != "--":
            w_line = f"{w_line}  {weather_reading.label}"
        style = _field_style(city, "weather", base_font_size, default_color)
        style["text"] = w_line
        segments.append(style)

    if city.get("show_notes", False):
        note = (city.get("notes") or "").strip()
        if note:
            note_style = _field_style(city, "notes", base_font_size, default_color)
            words = note.split()
            wrapped: list[str] = []
            current = ""
            for word in words:
                trial = f"{current} {word}".strip()
                if len(trial) > 26 and current:
                    wrapped.append(current)
                    current = word
                else:
                    current = trial
            if current:
                wrapped.append(current)
            for wrapped_line in wrapped[:3]:
                seg = dict(note_style)  # copy so per-line dicts don't share
                seg["text"] = wrapped_line
                segments.append(seg)

    if not segments:
        style = _field_style(city, "name", base_font_size, default_color)
        style["text"] = city.get("name", "Unnamed")
        segments.append(style)

    return segments


def _draw_marker_shape(draw: ImageDraw.ImageDraw, x: int, y: int, r: int,
                        color: tuple, style: str) -> None:
    outline = (20, 20, 20, 255)
    fill = (*color, 255)
    if style == "square":
        draw.rectangle([x - r, y - r, x + r, y + r], fill=fill, outline=outline, width=2)
    elif style == "diamond":
        draw.polygon([(x, y - r), (x + r, y), (x, y + r), (x - r, y)],
                     fill=fill, outline=outline)
    elif style == "star":
        import math as _math
        points = []
        for i in range(10):
            angle = -_math.pi / 2 + i * _math.pi / 5
            rr = r if i % 2 == 0 else r / 2.4
            points.append((x + rr * _math.cos(angle), y + rr * _math.sin(angle)))
        draw.polygon(points, fill=fill, outline=outline)
    elif style == "ring":
        draw.ellipse([x - r, y - r, x + r, y + r],
                     fill=None, outline=(*color, 255), width=max(2, r // 2))
    else:  # "dot" (default) - filled circle
        draw.ellipse([x - r, y - r, x + r, y + r], fill=fill, outline=outline, width=2)


def _draw_city_markers(img: Image.Image, cities: list[dict], now_utc: datetime,
                        center_lon: float, temp_units: str = "C",
                        weather_by_city: dict | None = None) -> Image.Image:
    """Draw a labeled marker for each city.

    Per-city fields honored (all optional):
        name, lat, lon, tz, color                    - required basics
        marker_style       - "dot" (default) | "square" | "diamond" | "star" | "ring"
        marker_size        - float multiplier, default 1.0
        label_side         - "right" (default) | "left" | "top" | "bottom" | "auto"
        label_offset_x/_y  - additional pixels to nudge the label
        show_name          - default True
        show_time          - default True
        show_weather       - default False
        show_notes         - default False
        notes              - free text
        text_color         - [R, G, B] override, default white
        background_alpha   - 0-255, default 165 (0 = fully transparent label bg)
        label_scale        - float multiplier for label font size, default 1.0
    """
    weather_by_city = weather_by_city or {}
    draw = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    base_font_size = max(11, w // 220)
    placed_boxes: list[tuple[int, int, int, int]] = []

    def _overlaps(box: tuple[int, int, int, int]) -> bool:
        for other in placed_boxes:
            if not (box[2] < other[0] or box[0] > other[2]
                    or box[3] < other[1] or box[1] > other[3]):
                return True
        return False

    for i, city in enumerate(cities):
        lon, lat = city["lon"], city["lat"]
        x, y = _lonlat_to_xy(lon, lat, w, h, center_lon)

        color = tuple(city.get("color", (255, 210, 60)))
        marker_style = city.get("marker_style", "dot")
        size_mult = float(city.get("marker_size", 1.0))
        marker_r = max(3, int((base_font_size // 3) * size_mult))
        _draw_marker_shape(draw, x, y, marker_r, color, marker_style)

        weather_reading = weather_by_city.get(i)
        default_text_color = tuple(city.get("text_color", (255, 255, 255)))
        segments = _build_label_segments(
            city, now_utc, weather_reading, temp_units,
            base_font_size, default_text_color,
        )

        # Measure the label as a block of stacked, per-segment styled lines.
        line_metrics = [draw.textbbox((0, 0), seg["text"], font=seg["font"])
                         for seg in segments]
        line_widths = [bbox[2] - bbox[0] for bbox in line_metrics]
        line_heights = [bbox[3] - bbox[1] for bbox in line_metrics]
        line_ybase = [bbox[1] for bbox in line_metrics]
        line_spacing = 3
        text_w = max(line_widths) if line_widths else 0
        text_h = sum(line_heights) + line_spacing * max(0, len(segments) - 1)

        pad_x, pad_y = 8, 5
        box_w = text_w + pad_x * 2
        box_h = text_h + pad_y * 2

        # Preferred anchor side. "auto" picks whichever side has more
        # horizontal room, matching the original behavior; the explicit
        # values let the user override that per city.
        side = city.get("label_side", "right")
        if side == "auto":
            side = "right" if x + marker_r + 8 + box_w <= w else "left"

        offset_x = int(city.get("label_offset_x", 0))
        offset_y = int(city.get("label_offset_y", 0))

        gap = 8
        if side == "left":
            label_x = x - marker_r - gap - box_w + offset_x
            base_y = y - box_h // 2 + offset_y
        elif side == "top":
            label_x = x - box_w // 2 + offset_x
            base_y = y - marker_r - gap - box_h + offset_y
        elif side == "bottom":
            label_x = x - box_w // 2 + offset_x
            base_y = y + marker_r + gap + offset_y
        else:  # "right"
            label_x = x + marker_r + gap + offset_x
            base_y = y - box_h // 2 + offset_y

        # Nudge vertically in steps if the preferred slot collides with
        # an already-placed label (up/down alternation, up to 6 steps).
        label_y = base_y
        step_h = box_h + 3
        placed = False
        for offset_steps in range(0, 7):
            for direction in (1, -1) if offset_steps else (1,):
                candidate_y = base_y + direction * offset_steps * step_h
                candidate = (label_x, candidate_y,
                             label_x + box_w, candidate_y + box_h)
                if (not _overlaps(candidate)
                        and candidate[0] >= 0 and candidate[2] <= w
                        and candidate[1] >= 0 and candidate[3] <= h):
                    label_y = candidate_y
                    placed = True
                    break
            if placed:
                break

        box = (label_x, label_y, label_x + box_w, label_y + box_h)
        placed_boxes.append(box)

        bg_alpha = int(city.get("background_alpha", 165))
        if bg_alpha > 0:
            draw.rounded_rectangle(list(box), radius=6, fill=(15, 15, 20, bg_alpha))

        # Draw each segment with its own font and colour.
        y_cursor = label_y + pad_y
        for idx, seg in enumerate(segments):
            draw.text((label_x + pad_x, y_cursor - line_ybase[idx]),
                       seg["text"], font=seg["font"],
                       fill=(*seg["color"], 255))
            y_cursor += line_heights[idx] + line_spacing

    return img


def render(output_path: str | Path, width: int, height: int,
           cities: list[dict], when: datetime | None = None,
           map_id: str = "blue_marble_july", center_lon: float = 0.0,
           twilight_width_deg: float = 7.0,
           night_darkness: float = 0.85,
           cloud_layer: Image.Image | None = None,
           cloud_opacity: float = 0.35,
           temp_units: str = "C",
           weather_by_city: dict | None = None) -> None:
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
                                      twilight_width_deg, night_darkness)
    composite = _roll_longitude(composite, center_lon)
    composite = composite.filter(ImageFilter.UnsharpMask(radius=1.5, percent=60, threshold=2))

    if cloud_layer is not None:
        composite = _apply_clouds(composite, cloud_layer, center_lon, cloud_opacity)

    composite = _draw_city_markers(composite, cities, when.astimezone(),
                                    center_lon, temp_units=temp_units,
                                    weather_by_city=weather_by_city)

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
