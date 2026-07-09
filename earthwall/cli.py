from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from .render import render
from .wallpaper import set_wallpaper, detect_desktop

DEFAULT_OUTPUT = Path.home() / ".cache" / "earthwall" / "current.png"
DEFAULT_CITIES = Path.home() / ".config" / "earthwall" / "cities.json"


def _load_cities(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def _detect_resolution() -> tuple[int, int]:
    """Best-effort primary monitor resolution via xrandr; falls back to 4K."""
    try:
        out = subprocess.run(["xrandr"], capture_output=True, text=True, check=True).stdout
        for line in out.splitlines():
            if " connected" in line and "primary" in line:
                for token in line.split():
                    if "x" in token and token[0].isdigit():
                        w, h = token.split("+")[0].split("x")
                        return int(w), int(h)
    except Exception:
        pass
    return 3840, 2160


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Live real-time Earth wallpaper - flat map, accurate "
                    "day/night terminator, city lights, and labeled markers "
                    "for chosen cities."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                         help="Where to write the rendered image.")
    parser.add_argument("--cities", type=Path, default=DEFAULT_CITIES,
                         help="Path to a cities JSON file (see cities.json example).")
    parser.add_argument("--width", type=int, default=None)
    parser.add_argument("--height", type=int, default=None)
    parser.add_argument("--interval", type=int, default=300,
                         help="Seconds between updates in daemon mode (default 300 = 5 min).")
    parser.add_argument("--once", action="store_true",
                         help="Render a single frame and exit (don't loop, don't set wallpaper).")
    parser.add_argument("--no-wallpaper", action="store_true",
                         help="Render only; don't attempt to set the desktop wallpaper.")
    parser.add_argument("--desktop", default=None,
                         help="Force a desktop environment (gnome/kde/xfce/cinnamon/mate/sway) "
                              "instead of auto-detecting.")
    args = parser.parse_args()

    if args.width and args.height:
        width, height = args.width, args.height
    else:
        width, height = _detect_resolution()

    cities = _load_cities(args.cities)
    if not cities:
        print(f"[earthwall] No cities loaded from {args.cities} - "
              f"copy the example cities.json there to add markers.", file=sys.stderr)

    def render_once(alternate: bool = False) -> None:
        # In daemon mode with wallpaper application we alternate between
        # two output files so the image the desktop is currently showing
        # is never rewritten in place (which flashes black mid-write on
        # most DEs). --once keeps the exact path the user asked for.
        if alternate:
            from .wallpaper import pick_next_wallpaper_path
            output = pick_next_wallpaper_path(args.output)
        else:
            output = args.output
        render(output, width, height, cities, when=datetime.now().astimezone())
        print(f"[earthwall] {datetime.now().strftime('%H:%M:%S')} rendered -> {output}")
        if not args.no_wallpaper:
            ok = set_wallpaper(output, desktop=args.desktop)
            if not ok:
                detected = args.desktop or detect_desktop()
                print(f"[earthwall] could not set wallpaper automatically "
                      f"(desktop detected as '{detected}'). Image is saved at "
                      f"{output} - set it manually or pass --desktop.",
                      file=sys.stderr)

    if args.once:
        render_once(alternate=False)
        return

    print(f"[earthwall] running as daemon, updating every {args.interval}s. Ctrl+C to stop.")
    while True:
        render_once(alternate=not args.no_wallpaper)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
