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
from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont

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
                   opacity: float, density: float = 1.0) -> Image.Image:
    """Overlay the live cloud alpha map.

    opacity (0..1): overall transparency of ALL cloud - a uniform fade.
    density (0..1): overall cloud COVERAGE - thins the cloud field itself.
        Implemented as an alpha threshold-and-restretch: wispy/thin cloud
        (low alpha) disappears first while dense storm cores survive, so
        at 50% density you still see distinct, solid-looking clouds - just
        fewer of them - rather than a uniformly ghostly haze (which is
        what lowering opacity alone gives you). Less physically real, far
        nicer than the whole desktop being blanked out under overcast.
    """
    cloud_layer = cloud_layer.resize(base.size, Image.LANCZOS)
    cloud_layer = _roll_longitude(cloud_layer, center_lon)

    r, g, b, a = cloud_layer.split()
    density = max(0.0, min(1.0, density))
    if density < 1.0:
        # Threshold rises as density falls; alpha below it is culled and
        # the remainder is re-stretched back to full range so surviving
        # cloud keeps its solid look instead of also going translucent.
        t = int(255 * (1.0 - density))
        if t >= 255:
            a = a.point(lambda p: 0)
        else:
            a = a.point(lambda p, t=t: 0 if p <= t else int((p - t) * 255 / (255 - t)))
    a = a.point(lambda p: int(p * opacity))
    cloud_layer = Image.merge("RGBA", (r, g, b, a))

    base = base.convert("RGBA")
    return Image.alpha_composite(base, cloud_layer).convert("RGB")


def _draw_hazards(img: Image.Image, center_lon: float,
                  earthquakes: list | None, hurricanes: list | None,
                  style: dict | None = None) -> Image.Image:
    """Draw earthquake and tropical-cyclone overlays on the map.

    `style` is an optional dict of display preferences (see the defaults
    in DEFAULT_HAZARD_STYLE). It controls marker shape, colour mode,
    size, and - for earthquakes - whether to print the magnitude number
    beside each marker and how to style that number.

    Both overlays are optional; passing None or an empty list draws
    nothing for that layer. Everything is wrapped so a malformed record
    can never break the render."""
    if not earthquakes and not hurricanes:
        return img

    st = dict(DEFAULT_HAZARD_STYLE)
    if style:
        st.update(style)

    w, h = img.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # ---- Earthquakes (weakest first so strongest end up on top) ----
    eq_size_mult = float(st.get("eq_size", 1.0))
    eq_shape = st.get("eq_shape", "circle")
    eq_color_mode = st.get("eq_color_mode", "magnitude")
    eq_custom = _hex_rgba(st.get("eq_color", "#FF3B30"), 190)
    eq_show_mag = bool(st.get("eq_show_magnitude", False))
    eq_mag_color = _hex_rgba(st.get("eq_mag_color", "#FFFFFF"), 255)
    eq_mag_size = float(st.get("eq_mag_text_size", 1.0))

    for q in reversed(earthquakes or []):
        try:
            mag = q.get("mag", 0.0)
            x, y = _lonlat_to_xy(q["lon"], q["lat"], w, h, center_lon)
            base = max(2.0, w / 900.0)
            r = int(base * (1.4 ** max(0.0, mag)) * eq_size_mult)
            r = max(2, min(r, int(w / 12)))
            color = eq_custom if eq_color_mode == "custom" else _quake_color(mag)
            _draw_marker(draw, eq_shape, x, y, r, color,
                         outline=(20, 20, 20, 200))
            if eq_show_mag:
                _draw_hazard_label(
                    draw, f"{mag:.1f}", x + r + max(2, r // 3), y - r,
                    fill=eq_mag_color,
                    size=max(10, int((w / 150) * eq_mag_size)))
        except Exception:
            continue

    # ---- Hurricanes ----
    hur_size_mult = float(st.get("hur_size", 1.0))
    hur_shape = st.get("hur_shape", "spiral")
    hur_color_mode = st.get("hur_color_mode", "category")
    hur_custom = _hex_rgba(st.get("hur_color", "#E91EA0"), 245)
    hur_show_name = bool(st.get("hur_show_name", True))
    hur_show_track = bool(st.get("hur_show_track", True))

    for s in (hurricanes or []):
        try:
            _draw_one_storm(draw, s, w, h, center_lon,
                            size_mult=hur_size_mult, shape=hur_shape,
                            color_mode=hur_color_mode, custom_color=hur_custom,
                            show_name=hur_show_name, show_track=hur_show_track)
        except Exception:
            continue

    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


# Default display preferences for the hazard overlays. The GUI writes a
# subset of these into settings["hazard_style"]; missing keys fall back
# here so older settings files keep working.
DEFAULT_HAZARD_STYLE = {
    # Earthquakes
    "eq_shape": "circle",         # circle | ring | dot | cross
    "eq_color_mode": "magnitude", # magnitude (ramp) | custom
    "eq_color": "#FF3B30",        # used when eq_color_mode == custom
    "eq_size": 1.0,               # marker size multiplier
    "eq_show_magnitude": False,   # print the magnitude number by each quake
    "eq_mag_color": "#FFFFFF",
    "eq_mag_text_size": 1.0,
    # Hurricanes
    "hur_shape": "spiral",        # spiral | ring | dot
    "hur_color_mode": "category", # category (ramp) | custom
    "hur_color": "#E91EA0",
    "hur_size": 1.0,
    "hur_show_name": True,
    "hur_show_track": True,
}


def _hex_rgba(hex_color: str, alpha: int) -> tuple:
    """Convert '#RRGGBB' to an (r, g, b, alpha) tuple; safe on bad input."""
    try:
        c = ImageColor.getrgb(hex_color)
        return (c[0], c[1], c[2], alpha)
    except Exception:
        return (255, 59, 48, alpha)


def _draw_marker(draw: "ImageDraw.ImageDraw", shape: str, x: int, y: int,
                 r: int, color: tuple, outline: tuple) -> None:
    """Draw one hazard marker in the requested shape."""
    if shape == "dot":
        rr = max(2, r // 2)
        draw.ellipse([x - rr, y - rr, x + rr, y + rr], fill=color)
    elif shape == "ring":
        draw.ellipse([x - r, y - r, x + r, y + r], outline=color,
                     width=max(2, r // 3))
    elif shape == "cross":
        wdt = max(2, r // 3)
        draw.line([x - r, y, x + r, y], fill=color, width=wdt)
        draw.line([x, y - r, x, y + r], fill=color, width=wdt)
    else:  # circle (default)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=color,
                     outline=outline, width=1)


def _draw_hazard_label(draw: "ImageDraw.ImageDraw", text: str, x: int, y: int,
                       fill: tuple, size: int) -> None:
    """Draw a small label with a dark outline for readability over any
    terrain (used for the earthquake magnitude number and storm names)."""
    try:
        font = _load_font(size)
    except Exception:
        return
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0, 210))
    draw.text((x, y), text, font=font, fill=fill)


def _quake_color(mag: float) -> tuple:
    """Yellow (small) -> orange -> red -> magenta (great), semi-transparent."""
    if mag < 3:
        return (255, 235, 59, 150)     # yellow
    if mag < 5:
        return (255, 152, 0, 170)      # orange
    if mag < 6.5:
        return (244, 67, 54, 185)      # red
    if mag < 8:
        return (211, 47, 47, 200)      # deep red
    return (233, 30, 99, 210)          # magenta for the great ones


# Category colour ramp for cyclones (TD..C5).
_CYCLONE_COLORS = {
    0: (120, 180, 255, 230),   # TD - pale blue
    1: (0, 200, 200, 235),     # TS - teal
    2: (0, 230, 120, 235),     # C1 - green
    3: (240, 230, 60, 240),    # C2 - yellow
    4: (255, 160, 30, 240),    # C3 - orange
    5: (255, 70, 70, 245),     # C4 - red
    6: (233, 30, 160, 250),    # C5 - magenta
}


def _draw_one_storm(draw: ImageDraw.ImageDraw, storm: dict, w: int, h: int,
                    center_lon: float, size_mult: float = 1.0,
                    shape: str = "spiral", color_mode: str = "category",
                    custom_color: tuple = (233, 30, 160, 250),
                    show_name: bool = True, show_track: bool = True) -> None:
    rank = storm.get("cat_rank", 0)
    color = (custom_color if color_mode == "custom"
             else _CYCLONE_COLORS.get(rank, (200, 200, 200, 235)))

    # Track polyline first, so the glyph sits on top of it.
    track = storm.get("_track_points") or []
    if show_track and len(track) >= 2:
        pts = [_lonlat_to_xy(lon, lat, w, h, center_lon) for lon, lat in track]
        # Break the line where it wraps the antimeridian to avoid a long
        # horizontal streak across the whole map.
        seg = [pts[0]]
        for prev, cur in zip(pts, pts[1:]):
            if abs(cur[0] - prev[0]) > w / 2:
                if len(seg) >= 2:
                    draw.line(seg, fill=(255, 255, 255, 150), width=max(1, w // 1400))
                seg = [cur]
            else:
                seg.append(cur)
        if len(seg) >= 2:
            draw.line(seg, fill=(255, 255, 255, 150), width=max(1, w // 1400))

    cx, cy = _lonlat_to_xy(storm["lon"], storm["lat"], w, h, center_lon)
    r = max(6, int((w / 130) + rank * max(2, w // 900)) * size_mult)

    if shape == "dot":
        rr = max(3, r // 2)
        draw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=color)
    elif shape == "ring":
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color,
                     width=max(2, r // 4))
    else:  # spiral (default): two curved arms + an eye
        bbox = [cx - r, cy - r, cx + r, cy + r]
        draw.arc(bbox, start=20, end=200, fill=color, width=max(2, r // 4))
        draw.arc(bbox, start=200, end=380, fill=color, width=max(2, r // 4))
        eye = max(2, r // 3)
        draw.ellipse([cx - eye, cy - eye, cx + eye, cy + eye],
                     fill=color, outline=(255, 255, 255, 230), width=1)

    # Name + category label beside the glyph.
    if show_name:
        label = f"{storm.get('name', '')} ({storm.get('category', '?')})".strip()
        if label:
            _draw_hazard_label(draw, label, cx + r + 4, cy - r,
                               fill=(255, 255, 255, 240),
                               size=max(11, int(w / 130)))


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


# Default label layout: each row is a list of fields that render side by
# side. Fields not turned on (show_*=False) are dropped; empty rows are
# skipped. If a city has no `label_layout` set, this is used.
DEFAULT_LABEL_LAYOUT = [
    ["name", "time"],
    ["weather"],
    ["notes"],
]


def _weather_text(weather_reading, temp_units: str, city: dict) -> str:
    """Build the weather line text, honoring per-city overrides.

    Supported weather-format overrides in the city dict:
      weather_show_emoji : bool (default True) - include the ☀/🌧/etc glyph
      weather_show_temp  : bool (default True) - include "24°C"
      weather_show_label : bool (default True) - include "Clear"/"Rain"
      weather_label_map  : dict {condition -> replacement} - lets the user
                          say "Sunny" instead of "Clear", "Wet" instead of
                          "Rain", etc. Case-insensitive lookup on the API's
                          label ("Clear", "Overcast", "Light rain", ...).
      weather_custom_format : str - overrides everything else; a Python
                          format string with placeholders {emoji}, {temp},
                          {label}, {code}. If set, that string is rendered
                          verbatim (with placeholders substituted).
    """
    if weather_reading is None:
        return ""

    label = weather_reading.label or ""
    label_map = city.get("weather_label_map") or {}
    if isinstance(label_map, dict) and label:
        # Case-insensitive key match; user might type "clear" or "Clear".
        for src, dst in label_map.items():
            if src.strip().lower() == label.strip().lower():
                label = dst
                break

    custom = city.get("weather_custom_format")
    if custom:
        try:
            return custom.format(
                emoji=weather_reading.emoji,
                temp=weather_reading.temp_display(temp_units),
                label=label,
                code=weather_reading.code,
            )
        except (KeyError, IndexError):
            pass  # Bad format string - fall through to the default composition.

    parts = []
    if city.get("weather_show_emoji", True) and weather_reading.emoji:
        parts.append(weather_reading.emoji)
    if city.get("weather_show_temp", True):
        parts.append(weather_reading.temp_display(temp_units))
    if city.get("weather_show_label", True) and label and label != "--":
        parts.append(label)
    return "  ".join(parts)


def _make_field_segment(field: str, city: dict, now_utc: datetime,
                         weather_reading, temp_units: str,
                         base_font_size: int, default_color: tuple) -> dict | None:
    """Build one styled segment for a given field (name/time/weather/notes),
    or return None if that field is disabled or has no content."""
    if field == "name":
        if not city.get("show_name", True):
            return None
        seg = _field_style(city, "name", base_font_size, default_color)
        seg["text"] = city.get("name", "Unnamed")
        return seg
    if field == "time":
        if not city.get("show_time", True):
            return None
        try:
            text = now_utc.astimezone(ZoneInfo(city["tz"])).strftime("%H:%M")
        except Exception:
            text = "--:--"
        seg = _field_style(city, "time", base_font_size, default_color)
        seg["text"] = text
        return seg
    if field == "weather":
        if not city.get("show_weather", False):
            return None
        text = _weather_text(weather_reading, temp_units, city)
        if not text:
            return None
        seg = _field_style(city, "weather", base_font_size, default_color)
        seg["text"] = text
        return seg
    if field == "notes":
        # Notes are special: one field can produce MULTIPLE wrapped lines.
        # Callers handle this via _build_label_rows below.
        return None
    return None


def _build_notes_segments(city: dict, base_font_size: int,
                           default_color: tuple) -> list[dict]:
    if not city.get("show_notes", False):
        return []
    note = (city.get("notes") or "").strip()
    if not note:
        return []
    style = _field_style(city, "notes", base_font_size, default_color)
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
    result = []
    for line in wrapped[:3]:
        seg = dict(style)
        seg["text"] = line
        result.append(seg)
    return result


def _build_label_rows(city: dict, now_utc: datetime,
                       weather_reading, temp_units: str,
                       base_font_size: int, default_color: tuple) -> list[list[dict]]:
    """Return a list of rows; each row is a list of styled segments that
    render horizontally next to each other. Honors `city["label_layout"]`
    if present, else uses DEFAULT_LABEL_LAYOUT.

    Rows that resolve to no visible segments are dropped; if the whole
    label ends up empty (all fields disabled), we fall back to just the
    name so no city ever renders as a bare marker with no text."""
    layout = city.get("label_layout") or DEFAULT_LABEL_LAYOUT
    rows: list[list[dict]] = []

    for row_fields in layout:
        row: list[dict] = []
        for field in row_fields:
            if field == "notes":
                # Notes rows expand into per-line segments below; the
                # layout row contributes one "notes" bucket that becomes
                # 1..N actual rows.
                for seg in _build_notes_segments(city, base_font_size, default_color):
                    rows.append([seg])
                continue
            seg = _make_field_segment(field, city, now_utc, weather_reading,
                                        temp_units, base_font_size, default_color)
            if seg is not None:
                row.append(seg)
        if row:
            rows.append(row)

    if not rows:
        seg = _field_style(city, "name", base_font_size, default_color)
        seg["text"] = city.get("name", "Unnamed")
        rows.append([seg])
    return rows


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
        rows = _build_label_rows(
            city, now_utc, weather_reading, temp_units,
            base_font_size, default_text_color,
        )

        # For each row, measure its total width (sum of segment widths +
        # gaps between them) and its height (max of segment heights).
        inter_field_gap = 12  # px between multiple fields on the same row
        line_spacing = 3

        row_metrics: list[dict] = []  # per-row {segments: [{seg, bbox}], w, h}
        for row in rows:
            seg_infos = []
            for seg in row:
                bbox = draw.textbbox((0, 0), seg["text"], font=seg["font"])
                seg_infos.append({
                    "seg": seg,
                    "bbox": bbox,
                    "w": bbox[2] - bbox[0],
                    "h": bbox[3] - bbox[1],
                    "ybase": bbox[1],
                })
            if not seg_infos:
                continue
            row_w = sum(s["w"] for s in seg_infos) + \
                     inter_field_gap * (len(seg_infos) - 1)
            row_h = max(s["h"] for s in seg_infos)
            row_metrics.append({"segs": seg_infos, "w": row_w, "h": row_h})

        text_w = max((r["w"] for r in row_metrics), default=0)
        text_h = sum(r["h"] for r in row_metrics) + \
                  line_spacing * max(0, len(row_metrics) - 1)

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

        # Draw each row: fields left-to-right, rows top-to-bottom.
        y_cursor = label_y + pad_y
        for row in row_metrics:
            x_cursor = label_x + pad_x
            # Vertically center each field within the row's height so a
            # small "24°C" doesn't sit off-baseline against a larger name.
            for s in row["segs"]:
                y_offset = (row["h"] - s["h"]) // 2
                draw.text((x_cursor, y_cursor + y_offset - s["ybase"]),
                           s["seg"]["text"], font=s["seg"]["font"],
                           fill=(*s["seg"]["color"], 255))
                x_cursor += s["w"] + inter_field_gap
            y_cursor += row["h"] + line_spacing

    return img


def _load_void_fill(color_hex: str, image_path: str | None,
                    size: tuple[int, int]) -> Image.Image:
    """Build the void-fill layer that shows outside the map area.

    Order of precedence: image if a valid path is provided, else a solid
    fill of the hex colour. Any exception loading the image silently
    falls back to the colour - a broken image path should never break
    wallpaper rendering."""
    w, h = size
    if image_path:
        try:
            img = Image.open(image_path).convert("RGB")
            # Cover the void area preserving aspect: scale to fill, crop.
            src_w, src_h = img.size
            src_ratio = src_w / max(1, src_h)
            dst_ratio = w / max(1, h)
            if src_ratio > dst_ratio:
                new_h = h
                new_w = int(round(h * src_ratio))
            else:
                new_w = w
                new_h = int(round(w / src_ratio))
            img = img.resize((new_w, new_h), Image.LANCZOS)
            left = (new_w - w) // 2
            top = (new_h - h) // 2
            return img.crop((left, top, left + w, top + h))
        except Exception:
            pass
    try:
        c = ImageColor.getrgb(color_hex)
    except Exception:
        c = (0, 0, 0)
    return Image.new("RGB", (w, h), c)


def _compose_multi_monitor(map_img: Image.Image, virtual_w: int, virtual_h: int,
                            map_w: int, map_h: int, map_x: int, map_y: int,
                            void_color: str, void_image: str | None) -> Image.Image:
    """Place a rendered map onto a virtual-desktop-sized canvas.

    `map_img` has already been rendered at (map_w, map_h). This function
    just handles the placement + void fill, so it stays cheap when the
    map exactly fills the desktop (no void areas at all).

    If the map fully covers the virtual desktop we skip the void layer
    entirely - saves memory and one Image allocation per render at the
    common 1.0x zoom / no offset case."""
    fully_covers = (map_x <= 0 and map_y <= 0
                    and map_x + map_w >= virtual_w
                    and map_y + map_h >= virtual_h)
    if fully_covers:
        # Just crop the map to the desktop rect - no void ever visible.
        crop_left = -map_x
        crop_top = -map_y
        return map_img.crop((crop_left, crop_top,
                             crop_left + virtual_w, crop_top + virtual_h))

    canvas = _load_void_fill(void_color, void_image, (virtual_w, virtual_h))
    canvas.paste(map_img, (map_x, map_y))
    return canvas


def _render_map_image(width: int, height: int, cities: list[dict],
                       when: datetime, sub_lat: float, sub_lon: float,
                       map_id: str, center_lon: float,
                       twilight_width_deg: float, night_darkness: float,
                       cloud_layer, cloud_opacity: float, cloud_density: float,
                       night_view: bool, temp_units: str,
                       weather_by_city: dict | None,
                       earthquakes: list | None = None,
                       hurricanes: list | None = None,
                       hazard_style: dict | None = None) -> Image.Image:
    """Produce a single (width x height) map image with day/night,
    clouds, hazard overlays, and city markers all applied. Extracted from
    render() so the independent multi-monitor branch can call it once per
    monitor with per-monitor parameters."""
    day_img, night_img = _load_maps(map_id)
    # Downsample to target size BEFORE the per-pixel day/night blend -
    # blend and unsharp then scale with the requested output size rather
    # than the ~5400x2700 source resolution.
    if day_img.size != (width, height):
        day_img = day_img.resize((width, height), Image.LANCZOS)
        night_img = night_img.resize((width, height), Image.LANCZOS)

    if night_view:
        composite = _composite_day_night(day_img, night_img, sub_lat, sub_lon,
                                          twilight_width_deg, night_darkness)
    else:
        composite = day_img.convert("RGB")
    composite = _roll_longitude(composite, center_lon)
    composite = composite.filter(ImageFilter.UnsharpMask(radius=1.5, percent=60, threshold=2))

    if cloud_layer is not None:
        composite = _apply_clouds(composite, cloud_layer, center_lon,
                                   cloud_opacity, cloud_density)

    # Hazard overlays sit above clouds (so a quake isn't hidden under an
    # opaque cloud) but below city markers (so your labelled cities stay
    # legible on top).
    composite = _draw_hazards(composite, center_lon, earthquakes, hurricanes,
                              hazard_style)

    composite = _draw_city_markers(composite, cities, when.astimezone(),
                                    center_lon, temp_units=temp_units,
                                    weather_by_city=weather_by_city)
    return composite


def _render_monitor_view(monitor, monitor_config: dict, global_center_lon: float,
                          global_center_lat: float, cities: list[dict],
                          when: datetime, sub_lat: float, sub_lon: float,
                          map_id: str, twilight_width_deg: float,
                          night_darkness: float, cloud_layer,
                          cloud_opacity: float, cloud_density: float,
                          night_view: bool, temp_units: str,
                          weather_by_city: dict | None,
                          earthquakes: list | None = None,
                          hurricanes: list | None = None,
                          hazard_style: dict | None = None) -> Image.Image:
    """Render a single monitor's view in "independent" mode, sized to
    that monitor's exact pixel dimensions.

    Per-monitor settings (zoom, focal lon/lat, void fill) come from
    `monitor_config`; anything missing falls back to the global setting,
    so switching mirror → independent doesn't blank a monitor that has
    no explicit config yet.

    Zoom > 1: render bigger than the monitor and centre-crop (shows less
    of the world, in more detail).
    Zoom < 1: render smaller than the monitor and pad with the monitor's
    own void fill (per-monitor colour/image applies here).
    Zoom = 1: render at monitor size, no cropping or padding.
    """
    zoom = monitor_config.get("zoom", 1.0)
    m_lon = monitor_config.get("center_lon", global_center_lon)
    m_lat = monitor_config.get("center_lat", global_center_lat)
    mw, mh = monitor.width, monitor.height
    # Per-monitor position offset (from that monitor's map_pos_x/y
    # spinboxes). Stored in the config as PIXELS relative to the real
    # monitor size; here we convert to a fraction of THIS render's size
    # so it works at both preview and full resolution. If the caller
    # passed a _ScaledMonitor proxy, mw/mh are already the preview-scaled
    # dimensions; the fraction stays correct because we divide by the
    # same reference the pixel value was entered against (the real
    # monitor size, stored on the underlying Monitor).
    real_w = getattr(monitor, "_m", monitor).width
    real_h = getattr(monitor, "_m", monitor).height
    pos_x_frac = monitor_config.get("map_pos_x", 0) / max(1, real_w)
    pos_y_frac = monitor_config.get("map_pos_y", 0) / max(1, real_h)
    render_w = max(1, int(round(mw * zoom)))
    render_h = max(1, int(round(mh * zoom)))

    img = _render_map_image(render_w, render_h, cities, when, sub_lat, sub_lon,
                             map_id, m_lon, twilight_width_deg, night_darkness,
                             cloud_layer, cloud_opacity, cloud_density,
                             night_view, temp_units, weather_by_city,
                             earthquakes, hurricanes, hazard_style)

    # Offset in pixels for this render's canvas.
    off_x = int(round(pos_x_frac * mw))
    off_y = int(round(pos_y_frac * mh))

    if zoom > 1.0:
        # Centre-crop to monitor size; latitude focal shifts the crop
        # window vertically, and the position offset shifts it further.
        crop_x = (render_w - mw) // 2 - off_x
        crop_y = (render_h - mh) // 2 - int(round(m_lat / 90.0 * (render_h - mh) / 2)) - off_y
        crop_x = max(0, min(render_w - mw, crop_x))
        crop_y = max(0, min(render_h - mh, crop_y))
        img = img.crop((crop_x, crop_y, crop_x + mw, crop_y + mh))
    elif zoom < 1.0:
        # Pad to monitor size with the monitor's void fill; the offset
        # shifts the pasted map, so an offset > 0 leaves void on the
        # opposite side (map_pos_y=100 → 100px void bar at the top).
        void = _load_void_fill(monitor_config.get("void_fill_color", "#000000"),
                                 monitor_config.get("void_fill_image"),
                                 (mw, mh))
        paste_x = (mw - render_w) // 2 + off_x
        paste_y = (mh - render_h) // 2 + int(round(m_lat / 90.0 * (mh - render_h) / 2)) + off_y
        void.paste(img, (paste_x, paste_y))
        img = void
    elif off_x != 0 or off_y != 0:
        # Zoom == 1.0 with an offset: the map is monitor-sized and we
        # shift it by (off_x, off_y), void-filling whatever's exposed.
        void = _load_void_fill(monitor_config.get("void_fill_color", "#000000"),
                                 monitor_config.get("void_fill_image"),
                                 (mw, mh))
        void.paste(img, (off_x, off_y))
        img = void
    return img


def _apply_monitor_overlay(img: Image.Image, layout, out_w: int, out_h: int,
                            map_rect: tuple | None = None) -> Image.Image:
    """Dim any part of `img` that doesn't overlap a physical monitor,
    outline each monitor with a subtle border, and (if `map_rect` is
    provided) draw a red outline around the map area itself. Used only
    for the in-app preview so the user can see, in one glance, which
    parts of the map will land on which screen - and where mismatched-
    monitor gaps leave map content off-screen. The red map outline
    matches the edit widget's red box visual language, so the two
    previews look and read the same. Purely visual, never applied to
    the real wallpaper output."""
    scale_x = out_w / max(1, layout.virtual_width)
    scale_y = out_h / max(1, layout.virtual_height)

    # 1. Build a mask that is opaque (255) inside every monitor and 0
    #    everywhere else. Anywhere the mask is 0 will be dimmed.
    mask = Image.new("L", (out_w, out_h), 0)
    mdraw = ImageDraw.Draw(mask)
    rects = []
    for m in layout.monitors:
        lx = int(round((m.x - layout.virtual_x) * scale_x))
        ly = int(round((m.y - layout.virtual_y) * scale_y))
        rw = max(1, int(round(m.width * scale_x)))
        rh = max(1, int(round(m.height * scale_y)))
        rects.append((lx, ly, lx + rw, ly + rh, m))
        mdraw.rectangle([lx, ly, lx + rw - 1, ly + rh - 1], fill=255)

    # 2. Build a much dimmer copy of the image. Compose original + dim
    #    version using the mask so monitor areas stay bright and the
    #    out-of-bounds areas become clearly-inactive at a glance.
    darkened = Image.eval(img.convert("RGB"), lambda v: v // 6)
    composited = Image.composite(img.convert("RGB"), darkened, mask)

    # 3. Draw a soft outline around every monitor + a numbered corner
    #    badge that matches the badges the Displays-tab edit widget
    #    draws in the same corner. Same numbers in the same positions
    #    in both views means any mismatch between preview and edit
    #    widget is immediately obvious ("monitor 2 is at the top-right
    #    here but bottom-left over there" - now visible at a glance).
    d = ImageDraw.Draw(composited)
    line_w = max(1, out_w // 900)
    badge_font = _load_font(max(11, out_w // 90))
    for lx, ly, rx, ry, m in rects:
        # Primary monitor gets a subtly warmer outline than secondaries -
        # a small extra cue that also matches the edit widget's red vs
        # grey "1" and "2" badge colours.
        outline_color = ((255, 100, 100) if m.is_primary
                         else (230, 230, 230))
        d.rectangle([lx, ly, rx - 1, ry - 1],
                    outline=outline_color, width=line_w)
        # Badge: small filled rect in the top-left of the monitor with
        # its 1-based index, so it lines up visually with the edit
        # widget which does exactly the same thing.
        badge_bg = ((200, 40, 40) if m.is_primary else (90, 90, 100))
        bx = lx + 3
        by = ly + 3
        bw = max(14, out_w // 80)
        bh = max(14, out_w // 80)
        d.rectangle([bx, by, bx + bw, by + bh], fill=badge_bg)
        # Center the number inside the badge.
        num_text = str(m.index + 1)
        try:
            tb = d.textbbox((0, 0), num_text, font=badge_font)
            tw = tb[2] - tb[0]
            th = tb[3] - tb[1]
        except Exception:
            tw, th = 8, 12
        d.text((bx + (bw - tw) / 2, by + (bh - th) / 2 - 1),
               num_text, font=badge_font, fill=(255, 255, 255))

    # 4. Red map-area outline (matches the Displays-tab edit widget). Any
    #    subtle mismatch between where the map actually lands here vs.
    #    where the edit widget predicts it becomes visible as two red
    #    boxes that don't align.
    if map_rect is not None:
        mx, my, mw, mh = map_rect
        # Clip so parts spilling off the canvas (zoom > 100%) don't error.
        rx1 = max(0, mx)
        ry1 = max(0, my)
        rx2 = min(out_w - 1, mx + mw - 1)
        ry2 = min(out_h - 1, my + mh - 1)
        if rx2 > rx1 and ry2 > ry1:
            d.rectangle([rx1, ry1, rx2, ry2],
                        outline=(230, 50, 50), width=max(2, out_w // 700))
    return composited


class _ScaledMonitor:
    """Wraps a Monitor with proportionally-scaled dimensions, so a low-
    resolution preview can render each monitor's independent view at a
    matching low-res size without touching the real Monitor object."""
    __slots__ = ("_m", "width", "height")
    def __init__(self, m, sx: float, sy: float):
        self._m = m
        self.width = max(1, int(round(m.width * sx)))
        self.height = max(1, int(round(m.height * sy)))
    # Delegate everything else to the underlying monitor.
    def __getattr__(self, name):
        return getattr(self._m, name)


def render(output_path: str | Path, width: int, height: int,
           cities: list[dict], when: datetime | None = None,
           map_id: str = "blue_marble_july", center_lon: float = 0.0,
           twilight_width_deg: float = 7.0,
           night_darkness: float = 0.85,
           cloud_layer: Image.Image | None = None,
           cloud_opacity: float = 0.35,
           cloud_density: float = 1.0,
           night_view: bool = True,
           temp_units: str = "C",
           weather_by_city: dict | None = None,
           # --- Multi-monitor (Phase 2.6) ---
           center_lat: float = 0.0,
           monitors_mode: str = "mirror",
           monitor_layout=None,  # earthwall.monitors.MonitorLayout | None
           map_zoom: float = 1.0,
           # Explicit map placement inside the virtual desktop. 0/0 =
           # "auto-centre" (the default). Any non-zero value pins the
           # map's top-left corner at those pixel coordinates - what the
           # Displays tab position spinboxes write.
           map_pos_x: int = 0,
           map_pos_y: int = 0,
           void_fill_color: str = "#000000",
           void_fill_image: str | None = None,
           # Full per-monitor config dict (only read in independent mode).
           # Keyed by monitor index as a string, matching monitors.py.
           monitor_configs: dict | None = None,
           # --- Hazard overlays ---
           earthquakes: list | None = None,
           hurricanes: list | None = None,
           hazard_style: dict | None = None,
           # When True (only used for the in-app preview), dim any part
           # of the composed image that doesn't land on a physical
           # monitor, and outline each monitor - so the preview reads as
           # "this is what will actually appear on my screens" instead
           # of showing the full compose canvas as if it were one flat
           # display. Only meaningful in span/independent mode with a
           # layout.
           preview_show_monitor_overlay: bool = False) -> None:
    """Render one wallpaper frame and save it to `output_path`.

    Modes:
      - "mirror" (default, back-compat): single image at (width, height).
      - "span": one map spanning the whole virtual desktop, honouring
        zoom / focal / void fill / explicit position.
      - "independent": each monitor renders its own view using its own
        per-monitor config (zoom, focal, void fill). Uncovered gaps
        between monitors (diagonal layouts) use the global void fill.
    """
    when = when or datetime.now().astimezone()
    sub_lat, sub_lon = subsolar_point(when)

    has_layout = (monitor_layout is not None and monitor_layout.virtual_width > 0)
    is_independent = monitors_mode == "independent" and has_layout
    # "span" covers both the explicit span mode AND mirror mode when a
    # layout was supplied because the user set a zoom/offset the flat
    # mirror render can't express. In that case we compose the single
    # map onto the virtual-desktop canvas so zoom / map_pos_x / map_pos_y
    # take effect (they're otherwise ignored by the flat render).
    is_span = (has_layout and not is_independent
               and monitors_mode in ("span", "mirror"))

    # Map placement rect in output-canvas pixels (map_x, map_y, map_w,
    # map_h). Populated by whichever branch runs and passed to the
    # preview overlay so it can draw a red outline showing exactly where
    # the map lands - matches the edit widget's red box visual language,
    # so the two previews look and read the same.
    map_rect_for_overlay: tuple[int, int, int, int] | None = None

    # ---- Independent mode: render each monitor separately ----
    if is_independent:
        # Scale monitor rects to fit the requested output size (usually
        # matches the virtual desktop, but for the low-res preview it
        # doesn't - and rendering the full virtual desktop for every
        # preview tick would be needlessly slow).
        scale_x = width / max(1, monitor_layout.virtual_width)
        scale_y = height / max(1, monitor_layout.virtual_height)
        virtual_w, virtual_h = width, height
        canvas = _load_void_fill(void_fill_color, void_fill_image,
                                   (virtual_w, virtual_h))
        cfgs = monitor_configs or {}
        for m in monitor_layout.monitors:
            cfg = cfgs.get(str(m.index), {})
            # Scale each monitor's rendered image proportionally.
            scaled_m = _ScaledMonitor(m, scale_x, scale_y)
            mon_img = _render_monitor_view(
                scaled_m, cfg, center_lon, center_lat, cities, when, sub_lat, sub_lon,
                map_id, twilight_width_deg, night_darkness, cloud_layer,
                cloud_opacity, cloud_density, night_view, temp_units,
                weather_by_city, earthquakes, hurricanes, hazard_style,
            )
            lx = int(round((m.x - monitor_layout.virtual_x) * scale_x))
            ly = int(round((m.y - monitor_layout.virtual_y) * scale_y))
            canvas.paste(mon_img, (lx, ly))
        composite = canvas

    # ---- Span mode: one map across the whole virtual desktop ----
    elif is_span:
        # Render at the CALLER's requested size (width, height), not the
        # layout's virtual size. The layout's job here is to give us the
        # semantics (single-image span vs per-monitor, and the aspect
        # ratio to render at); the actual pixel dimensions come from the
        # caller so previews stay small/fast and full renders stay full-
        # sized. Position offsets are fractions of the output, so the
        # same offset produces a proportionally identical result at
        # every resolution.
        #
        # The MAP itself is drawn at its natural 2:1 equirectangular
        # aspect ratio - stretching it to fit an odd virtual-desktop
        # aspect (very wide multi-monitor setups, portrait orientations)
        # would squash the world. Instead, at zoom 1.0 we fit the map
        # into the desktop preserving 2:1, and any leftover area is
        # filled with the void colour/image. Zoom scales from there:
        # zoom > 1 makes the map larger than that fit (spills off the
        # edges), zoom < 1 makes it smaller (more void showing).
        virtual_w, virtual_h = width, height
        fit_w = min(virtual_w, virtual_h * 2)
        fit_h = fit_w // 2
        map_w = max(1, int(round(fit_w * map_zoom)))
        map_h = max(1, int(round(fit_h * map_zoom)))
        if map_pos_x != 0 or map_pos_y != 0:
            map_x = int(round(map_pos_x * virtual_w)) + (virtual_w - map_w) // 2
            map_y = int(round(map_pos_y * virtual_h)) + (virtual_h - map_h) // 2
        else:
            map_x = (virtual_w - map_w) // 2
            map_y = (virtual_h - map_h) // 2 + int(round(center_lat / 90.0 * map_h / 2))
        map_img = _render_map_image(
            map_w, map_h, cities, when, sub_lat, sub_lon, map_id, center_lon,
            twilight_width_deg, night_darkness, cloud_layer, cloud_opacity,
            cloud_density, night_view, temp_units, weather_by_city,
            earthquakes, hurricanes, hazard_style,
        )
        composite = _compose_multi_monitor(
            map_img, virtual_w, virtual_h, map_w, map_h, map_x, map_y,
            void_fill_color, void_fill_image,
        )
        map_rect_for_overlay = (map_x, map_y, map_w, map_h)

    # ---- Mirror mode (default): classic single-image render ----
    else:
        composite = _render_map_image(
            width, height, cities, when, sub_lat, sub_lon, map_id, center_lon,
            twilight_width_deg, night_darkness, cloud_layer, cloud_opacity,
            cloud_density, night_view, temp_units, weather_by_city,
            earthquakes, hurricanes, hazard_style,
        )

    # Preview-only overlay: dim off-monitor areas and outline monitors so
    # the preview shows exactly which parts of the composed image will
    # actually land on a physical screen.
    if preview_show_monitor_overlay and monitor_layout is not None \
            and (is_span or is_independent):
        composite = _apply_monitor_overlay(composite, monitor_layout,
                                            width, height,
                                            map_rect_for_overlay)

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
