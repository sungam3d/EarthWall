"""
Font discovery for label rendering.

Instead of hardcoding one font path, this module scans the system's font
directories once at startup, groups fonts by family (DejaVu Sans, Liberation
Sans, Noto Sans, etc.) and style (Regular/Bold/Italic/BoldItalic), and
caches the results in memory. The city dialog uses `list_families()` to
build a dropdown; the renderer uses `resolve()` to turn a
(family, style, size) tuple into an actual PIL FreeTypeFont.

Everything is best-effort: if the requested family isn't available, we
fall back through DejaVu Sans (near-universal), then Liberation Sans,
then any monospace-or-sans-ish thing we can find, then PIL's default
bitmap font. Rendering never fails just because a font is missing.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

# Directories to scan. Covers Debian/Ubuntu, Fedora, Arch, and per-user
# fonts. Non-existent entries are skipped silently.
_FONT_DIRS = [
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    str(Path.home() / ".fonts"),
    str(Path.home() / ".local/share/fonts"),
]

# Curated list of families to surface in the picker, in preference order.
# Others are still discoverable via full-name entry, but this keeps the
# dropdown from having 200 items on a well-populated system.
_PREFERRED_FAMILIES = [
    "DejaVu Sans", "DejaVu Serif", "DejaVu Sans Mono",
    "Liberation Sans", "Liberation Serif", "Liberation Mono",
    "Noto Sans", "Noto Serif", "Noto Sans Mono",
    "Ubuntu", "Ubuntu Mono", "Ubuntu Condensed",
    "Cantarell", "Carlito", "Caladea",
    "FreeSans", "FreeSerif", "FreeMono",
]

STYLES = ["Regular", "Bold", "Italic", "BoldItalic"]


def _parse_font_filename(path: Path) -> tuple[str, str] | None:
    """Parse a font-family and style from a filename like
    'LiberationSans-BoldItalic.ttf' or 'DejaVuSans-Bold.ttf'.
    Returns (family, style) or None if we can't guess sensibly."""
    stem = path.stem
    # Some fonts use underscores or spaces; normalise to hyphen splits.
    for sep in ("_", " "):
        stem = stem.replace(sep, "-")

    parts = stem.split("-")
    if not parts:
        return None

    # Detect style suffix from the last chunk.
    last = parts[-1].lower()
    style = "Regular"
    if last in ("bold",):
        style = "Bold"; parts = parts[:-1]
    elif last in ("italic", "oblique"):
        style = "Italic"; parts = parts[:-1]
    elif last in ("bolditalic", "boldoblique"):
        style = "BoldItalic"; parts = parts[:-1]
    elif last in ("regular", "roman", "book", "medium"):
        # Skip the marker, keep the family part.
        parts = parts[:-1]

    if not parts:
        return None

    # Turn "LiberationSans" -> "Liberation Sans" by splitting on
    # lower-then-upper transitions.
    family_raw = "-".join(parts)
    family = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", family_raw)
    family = family.replace("-", " ").strip()

    # Reunite known compound family names that get chopped by the naive
    # camelCase split. E.g. "DejaVu Sans" comes out as "Deja Vu Sans"
    # otherwise, which then no longer matches _PREFERRED_FAMILIES.
    compound_fixes = {
        "Deja Vu": "DejaVu",
        "Free Sans": "FreeSans",
        "Free Serif": "FreeSerif",
        "Free Mono": "FreeMono",
    }
    for wrong, right in compound_fixes.items():
        if family.startswith(wrong):
            family = right + family[len(wrong):]
    return family, style


@lru_cache(maxsize=1)
def _scan_fonts() -> dict[str, dict[str, str]]:
    """Return {family: {style: path}}.  Cached for the lifetime of the process."""
    families: dict[str, dict[str, str]] = {}
    for base in _FONT_DIRS:
        base_path = Path(base)
        if not base_path.exists():
            continue
        for ttf in base_path.rglob("*.ttf"):
            parsed = _parse_font_filename(ttf)
            if not parsed:
                continue
            family, style = parsed
            families.setdefault(family, {})
            # Prefer the first path we see for a given family+style, so
            # /usr/share overrides don't get shadowed by user fonts.
            families[family].setdefault(style, str(ttf))
    return families


def list_families() -> list[str]:
    """Available font family names, with preferred ones first."""
    found = _scan_fonts()
    preferred = [f for f in _PREFERRED_FAMILIES if f in found]
    others = sorted(f for f in found if f not in _PREFERRED_FAMILIES)
    return preferred + others


def available_styles(family: str) -> list[str]:
    """Which of Regular/Bold/Italic/BoldItalic are actually installed
    for a given family. Always includes Regular so the UI never shows an
    empty style list."""
    fonts = _scan_fonts().get(family, {})
    styles = [s for s in STYLES if s in fonts]
    if not styles:
        styles = ["Regular"]
    return styles


def _fallback_font_path() -> str | None:
    """Absolute last-resort: any sans-ish font we can find, for use when
    the user's chosen family isn't installed at all."""
    for family in ("DejaVu Sans", "Liberation Sans", "Noto Sans", "FreeSans"):
        fonts = _scan_fonts().get(family, {})
        for style in ("Regular", "Bold"):
            if style in fonts:
                return fonts[style]
    # Really last resort: return the first font found at all.
    for family_fonts in _scan_fonts().values():
        for path in family_fonts.values():
            return path
    return None


def resolve(family: str, style: str, size: int) -> ImageFont.FreeTypeFont:
    """Load a font as a PIL FreeTypeFont, falling back gracefully if the
    requested family/style isn't installed. `size` is in pixels."""
    size = max(6, int(size))

    fonts_for_family = _scan_fonts().get(family, {})
    path = fonts_for_family.get(style)

    if path is None:
        # Try alternative style within the same family (e.g. requested Bold
        # but only Regular exists).
        for alt in ("Regular", "Bold", "Italic", "BoldItalic"):
            if alt in fonts_for_family:
                path = fonts_for_family[alt]
                break

    if path is None:
        path = _fallback_font_path()

    if path is None:
        return ImageFont.load_default()

    try:
        return ImageFont.truetype(path, size)
    except (OSError, ValueError):
        return ImageFont.load_default()


DEFAULT_FAMILY = "DejaVu Sans"
