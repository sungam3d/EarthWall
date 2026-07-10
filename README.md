![EarthWall](assets/Icon/Transparent/convertico-icon-transparent_128x128.png)
# EarthWall

A live, real-time Earth wallpaper for Linux: flat equirectangular map,
astronomically accurate day/night terminator, night-side city lights,
and richly customisable city markers showing local time, current weather,
and personal notes — all managed through a proper desktop app.

Built as a Linux-native alternative to DeskSoft's EarthView, using real
NASA Blue Marble / Black Marble imagery.

## Screenshots

### Desktop wallpaper examples

![Full desktop screenshot 1](assets/screenshots/SS-01.png)
![Full desktop screenshot 2](assets/screenshots/SS-02.png)
![Full desktop screenshot 3](assets/screenshots/SS-03.png)
![Full desktop screenshot 4](assets/screenshots/SS-04.png)

### Application interface

**General tab**

![General tab](assets/screenshots/SS-General.jpg)

**Map & Views tab**

![Map & Views tab](assets/screenshots/SS-Map-Views.jpg)

**Cities tab**

![Cities tab](assets/screenshots/SS-Cities.jpg)

## What's in the GUI

### Live preview and updates

- **Live preview** of the current render at a fixed 2:1 aspect ratio (no
  resizing or flicker as settings change), with a one-click "Update Now",
  a pause/resume toggle, and a countdown to the next automatic update.
- The preview re-renders instantly (at low resolution) whenever you
  change any setting, with an overlay busy indicator while it works — the
  progress bar is drawn *over* the preview so it never causes layout to
  shift.
- **Flicker-free wallpaper updates** — renders are written atomically
  and alternate between two files, so the image your desktop is
  displaying is never touched mid-write. No more flash-to-black
  transitions; you get your desktop environment's normal near-instant
  wallpaper swap.
- **Click-to-jump sliders** everywhere — click anywhere on a slider's
  groove to jump the handle there instantly, or drag as normal.

### Maps and rendering

- **Map picker** — switch between the bundled seasonal maps (July /
  December Blue Marble), or **import your own** day/night image pair.
  Almost any image format works (JPEG, PNG, BMP, TIFF, WEBP, GIF) —
  it's decoded and converted automatically. If you only supply a day
  map, the bundled night-lights map is paired with it automatically.
- **Map re-centering** — a slider/spinner to shift which longitude sits
  in the middle of the map, with quick presets (Americas, Atlantic, Asia,
  Pacific / Australia).
- **Day/night edge softness slider** — from a crisp terminator line to
  a wide, soft dusk band.
- **Night side darkness slider** — controls how dark the unlit half of
  the world gets. City lights always stay bright and vivid; only the
  unlit landscape and ocean darken, so you can dial from a soft
  "blue-hour" look through to a genuine deep-black night with just
  glowing cities.
- **Live cloud overlay** (optional) — a free, near-real-time global
  cloud layer that updates every ~3 hours, with an opacity slider. If a
  refresh fails (offline, service hiccup), the last good cloud layer
  keeps being used rather than clouds blinking off.

### City markers

- **Searchable city database** of ~130 major cities — pick one and its
  coordinates and timezone fill in automatically. Anywhere not listed
  can still be entered manually (name / latitude / longitude / timezone).
- **Marker shapes** — dot, ring, square, diamond, or star, with a size
  multiplier per city.
- **Label placement** — choose which side of the marker the label sits
  on (right / left / above / below / auto), with pixel-level nudge
  controls for fine positioning.
- **Flexible label layout** — build the label row by row. Choose a
  preset (name + time / weather / notes; everything separate; name /
  time + weather; and a few more) or switch to Custom and tick which
  fields go on which row. Fields on the same row render side by side.
- **Per-field text styling** — the name, time, weather, and notes lines
  each get their own font family, style (Regular / Bold / Italic /
  BoldItalic), size multiplier, and colour. Available fonts are
  discovered from the system automatically.
- **Weather per city** — powered by the free Open-Meteo API (no account
  needed), cached per city and refreshed automatically. Each city with
  weather enabled shows in the Cities tab with a live status column so
  you can tell "loading…" apart from "unavailable" apart from an actual
  reading like "☀ 24°C".
- **Custom weather text** — toggle each part on or off (icon /
  temperature / condition), or rename any condition to your own
  wording ("Sunny" instead of "Clear", "Wet" instead of "Rain", etc.).
  For full control, an advanced custom format string lets you write
  arbitrary templates like `{emoji} it's {temp} and {label} outside`.
- **Notes per city** — free-text field displayed under the marker,
  wrapped and capped so it doesn't dominate the map. Handy for
  "Sarah's flat", "office hours 9-5", timezone offsets, whatever.
- **Automatic label collision avoidance** — clustered cities like
  London / Paris / Amsterdam spread their labels vertically instead of
  overlapping.

### Runs as a proper desktop app

- **System tray icon** — closing the settings window just hides it; the
  wallpaper keeps auto-updating in the background. Right-click the tray
  icon for quick actions (open settings, update now, pause, quit).
- **Start at login** — one checkbox, no manual systemd setup required.
- **Applications menu entry** added automatically on first launch, so
  you can search "EarthWall" in your app launcher like any other program.

## Install

```bash
cd earthwall
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Run it

```bash
./run_gui.sh
```

(or, equivalently: `source venv/bin/activate && python -m earthwall.gui`)

The first launch also adds EarthWall to your applications menu, so after
that first run you can just search for "EarthWall" in your app launcher
like any other program — no terminal needed.

A tray icon appears (a little globe). The settings window opens
automatically the first time; after that, click the tray icon to
show/hide it. Use the **General** tab to turn on "Start automatically
when I log in" once you're happy with your setup.

## Adding cities

Cities tab → **Add City…** → start typing a name in the search box (e.g.
"Tokyo") and pick it from the suggestions — coordinates and timezone
fill in automatically. Pick a marker colour and hit OK. For a city not
in the built-in list, just fill in the name / latitude / longitude /
timezone fields directly (run `timedatectl list-timezones` in a terminal
if you need to look up the exact timezone name).

Each city then has its own tabs for **Marker** (shape, size),
**Placement** (label side, nudge, background), **Layout** (which fields
appear on which rows), **Text styling** (per-field font choices),
**Weather** (formatting and condition renaming), and **Notes**. Most
cities only ever need Basics — everything else is there when you want
it.

## Adding your own map

Map & View tab → **Import New Map…** → choose a day-view image (ideally
a flat, 2:1 width:height equirectangular map — the app will gently warn
you if the aspect ratio looks unusual, but won't stop you). A night map
is optional. Give it a name and hit OK — it becomes another entry in
the map list, selectable and deletable like any other (built-in maps
can't be deleted).

## Advanced / headless use (no GUI)

The original command-line tool still works standalone, useful for
servers or scripting:

```bash
python -m earthwall.cli --once --no-wallpaper --output ~/test.png
python -m earthwall.cli --interval 300   # run as a foreground daemon
```

See `python -m earthwall.cli --help` for all options. It reads and
writes the same `~/.config/earthwall/cities.json` the GUI uses, so
switching between the two is seamless.

## How it works

- `earthwall/sun.py` calculates the "subsolar point" (where the sun is
  directly overhead) using the standard NOAA solar position formulas.
- `earthwall/render.py` blends the day and night maps together across a
  soft twilight band along the terminator, optionally re-centers the map
  on a chosen longitude, layers in live clouds if enabled, and draws
  city markers with fully customisable multi-row styled labels on top.
- `earthwall/maps.py` manages built-in and user-imported map sets, and
  handles decoding and validating whatever image format you throw at it.
- `earthwall/clouds.py` fetches the optional live cloud layer, with
  atomic writes, thread-safe access, and a last-known-good fallback so
  clouds never randomly blink off between updates.
- `earthwall/weather.py` fetches per-city weather from Open-Meteo, with
  a per-city cache, retry backoff, and a non-blocking cached-read API
  for the GUI status column.
- `earthwall/fonts.py` scans the system's font directories at startup
  and provides a stable family / style lookup for label rendering,
  falling back gracefully if a chosen font isn't installed.
- `earthwall/wallpaper.py` detects your desktop environment (GNOME, KDE
  Plasma, XFCE, Cinnamon, MATE, or a generic X11 WM) and sets the
  rendered image as your wallpaper the right way for each, alternating
  between two output files for flicker-free updates.
- `earthwall/gui.py` + `earthwall/gui_main_window.py` are the PySide6
  desktop app: a system tray icon plus a settings window, with renders
  running on a background thread so the UI never freezes.
- `earthwall/gui_widgets.py` provides shared custom widgets, notably a
  click-to-jump QSlider subclass used everywhere in the app.
- `earthwall/autostart.py` manages the XDG autostart entry (login) and
  application-menu entry (launcher), both via standard `.desktop` files
  that work across desktop environments.

## Attribution

Day and night map imagery is NASA's public-domain "Blue Marble" and
"Black Marble" (city lights) datasets. The optional live cloud layer is
sourced from the free [live-cloud-maps](https://github.com/matteason/live-cloud-maps)
project, built on public satellite data. Weather data comes from
[Open-Meteo](https://open-meteo.com), a free weather API released under
CC BY 4.0.
