# EarthWall v1.0.07 — Work Plan & Checklist
(Keep this file in the repo. Each session: read this first, tick items, bump notes.)

## Phase 1 — Bug fixes & small features (this session)
- [x] **1.1 Preview stretch bug** — preview renders hardcoded 1280x640 (2:1) but
      "Update now" renders at real screen aspect. Fix: in gui_main_window.py,
      compute preview size from `_current_resolution()` aspect (width 1280,
      height = round(1280 * h/w)); also make `_update_preview_size()` use that
      same aspect instead of `// 2`. Re-render preview when resolution changes.
- [x] **1.2 Window sizing pass** — audit all windows/dialogs so no settings are
      cut off: main window (each tab), gui_city_dialog.py, gui_map_dialog.py.
      Use minimumSizeHint/adjustSize, sensible setMinimumSize, and scroll areas
      where tabs are tall.
- [x] **1.3 Cloud density control** — new slider "Cloud density" (0–100%).
      Implementation: threshold/attenuate the cloud alpha channel, e.g.
      alpha' = clip((alpha - t) / (1 - t)) with t derived from density, so thin
      cloud disappears first and dense cores remain. Keep opacity as overall
      transparency; density controls coverage. Settings key: `cloud_density`.
- [x] **1.4 Night view toggle** — checkbox "Show night side" (settings key
      `night_view`, default on). When off, render full-day map (skip day/night
      mask blend, or mask weight = 1.0 day).

## Phase 2 — Multi-monitor support ✅ COMPLETE
- [x] **2.1 Monitor detection layer** — enumerate QScreens (geometry, position,
      resolution, aspect). Model: virtual desktop bounding box + per-monitor rects
      (handles diagonal/offset layouts like EarthView-04). **Done in monitors.py.**
- [x] **2.2 Settings schema** — `monitors_mode`: "mirror" | "span" | "independent";
      per-monitor map config for independent mode. **Done: defaults in settings.py,
      helpers in monitors.py (monitor_config_for / set_monitor_config).**
- [x] **2.3 Preview UI rework** — screen-area preview showing each monitor as an
      outlined rect inside the virtual desktop grid; two previews (or combined
      grid view) when 2 monitors detected, each at its own aspect.
      **Done in gui_display_widgets.py (ScreenAreaPreview). Handles diagonal
      layouts correctly (verified against EarthView-04 numbers).**
- [x] **2.4 Map placement controls** — zoom slider, pan up/down as well as
      left/right (map offset x/y), draggable red focal-point dot on the preview
      (EarthView style). **DONE: draggable red-dot focal picker updates
      center_lon/center_lat live; zoom slider 50-400% stored in
      monitor_configs[N]["zoom"]; position spinboxes pin exact placement;
      Auto-center button. Per-monitor independent editing done (2.7).**
- [x] **2.5 Void fill** — DONE: colour swatch + image chooser + clear button +
      status label; renderer honours image with aspect-preserving scale+crop
      and per-monitor void fill applied on padded monitors in independent mode.
- [x] **2.6 Renderer changes** — DONE: mirror unchanged, span composes onto
      virtual-desktop canvas with void fill, wallpaper.py spanned=True flag
      sets DE-appropriate options (GNOME/Cinnamon/MATE picture-options=spanned,
      feh --no-xinerama, swaybg fit).
- [x] **2.7 True per-monitor independent editing** — each monitor renders its
      own view at its own dimensions with its own zoom/focal/void. New helpers
      _render_map_image and _render_monitor_view extracted; independent mode
      composites each monitor's render onto the virtual-desktop canvas at its
      virtual position. UI: "Editing monitor" dropdown appears only in
      independent mode; controls route through active monitor's config;
      switching targets reloads control values; per-monitor void fill applies
      to padding when a monitor's zoom < 100%; global void fill still applies
      to gaps between monitors in diagonal layouts. Verified end-to-end.

## Phase 3 — GUI redesign (2026-07-11)
- [x] **3.1 General tab: start-in-tray** — added "Start hidden in the system
      tray" checkbox (sub-option under autostart). Setting start_in_tray;
      gui.py skips window.show() when set and shows a tray balloon instead.
- [x] **3.2 Clouds & Weather tab** — split live clouds, cloud opacity/density,
      night side, and city-weather (temp units) out of General into their own
      tab. Tab order: General, Map & View, Clouds & Weather, Displays, Cities.
- [x] **3.3 Map & View: draggable dot replaces center slider** — moved the
      MapFocalPointPreview (red-dot picker) from Displays to Map & View,
      replacing the old longitude slider (dot does lon AND lat, strictly
      better). Old slider/spinbox kept as hidden widgets for load/save compat;
      preset buttons ("Americas/Atlantic/Asia/Pacific") now move the dot too.
- [x] **3.4 Displays: taller screen area** — screen-area preview min height
      220→340 with stretch, so a whole monitor stays visible when zoom < 100%
      shrinks the map rect. Fixed inverted-zoom bug: map-area rect was
      vw/zoom (grew when zooming out -> wide+short box); now vw*zoom (shrinks
      when zooming out), matching the renderer.
- [x] **3.5 CRITICAL: zoom/position now reach the desktop** — root cause:
      _render_layout() returned None in mirror mode, and render() only
      honoured zoom/pos in span/independent (both need a layout), so the
      Displays zoom + X/Y controls updated the preview but were silently
      dropped from the actual wallpaper render. Fix: _render_layout() now
      returns a layout whenever zoom != 1.0 or map offset != 0 (even in
      mirror mode), and render() treats "mirror + layout" as span (composes
      the single map onto the virtual-desktop canvas honouring zoom/pos/void).
      Default case (mirror, 100%, no offset) still returns None -> fast path.
      Verified: mirror+zoom50 shows void border in the real render output.

## Phase 4 — GUI polish + number inputs (2026-07-11)
- [x] **4.1 Screen-area zoom behaviour** — monitor rectangle now stays a
      FIXED size at every zoom level; only the map rect scales, clipped to
      the widget so overflow (zoom>100%) bleeds off-edge instead of forcing
      a rescale that shrank the monitor. _fit_transform fits only the virtual
      desktop (never the map area); map drawn under a setClipRect; badge
      pinned to the visible region. Verified monitor rect identical at
      50/100/300% zoom.
- [x] **4.2 LabeledSlider widget** — new gui_widgets.LabeledSlider pairs a
      ClickJumpSlider with a synced QSpinBox (two-way, feedback-loop-safe),
      exposing a plain-slider-compatible value()/setValue()/valueChanged API.
      setValue() is silent (load path safe); user edits emit once.
- [x] **4.3 Number inputs everywhere** — converted to LabeledSlider: cloud
      opacity, cloud density, twilight (sharp), night darkness (shadows),
      map zoom. Added dedicated X/Y (lon/lat) number boxes for the map-center
      red dot, synced both ways with dragging. Removed all the old read-only
      "value_label" QLabels these replace.
- [x] **4.4 README** — refreshed: five-tab overview, multi-monitor modes,
      cloud opacity vs density, night toggle, start-in-tray, draggable dot +
      number boxes, crash log; added monitors.py / gui_display_widgets.py to
      the module list; fixed stale "2:1 preview" note.
- [x] **4.5 Code review** — py_compile + pyflakes clean across all modules;
      removed dead imports (QSlider/QDoubleSpinBox in main window, QSlider in
      city dialog, QEvent in widgets, Optional in display widgets) and two
      dead locals (gui_worker temp_units, autostart module_dir). Verified
      _render_layout engagement logic (default=None fast path; zoom/offset/
      independent engage), focal extremes, and silent-load behaviour.

## Reference notes
- Bug repro: Update now → move cloud opacity → preview stretches; Update now fixes.
  Root cause confirmed in code (hardcoded 2:1 preview vs screen-aspect wallpaper).
- User's reference screenshots (EarthView-01..04.jpg) were mentioned but NOT
  received in session 1 — ask user to re-attach before Phase 2.3/2.4.
- Cloud source: clouds.matteason.co.uk alpha PNG, 4096x2048, cached 3h.
- Version bump: earthwall/__init__.py → 1.0.07 when Phase 1 lands.

## Session log
- Session 1 (2026-07-10): Phase 1 COMPLETE (1.1-1.4 implemented + smoke-tested headless). Version bumped to 1.0.07. Next session: start Phase 2.1; ask user to re-attach EarthView-01..04.jpg screenshots.
- Session 2 (2026-07-10): Added first-load spinner with explanatory text (animated dots + "why this is slow" note; stops on first pixmap). Phase 2.1 + 2.2 complete: monitors.py module with Monitor / MonitorLayout dataclasses, detect_layout(), virtual-desktop bounding box (preserves negative offsets for diagonal setups), per-monitor config helpers. Settings schema extended with monitors_mode + monitor_configs. Version 1.0.08. Phase 2.3 blocked on user re-attaching EarthView-01..04.jpg screenshots.
- Session 3 (2026-07-11): Got EarthView screenshots. New "Displays" tab in main window. New gui_display_widgets.py with ScreenAreaPreview (renders virtual desktop + monitors + red-outlined map area, verified against diagonal EarthView-04 numbers) and MapFocalPointPreview (draggable red dot updating center_lon/center_lat live). Mode selector (mirror/span/independent), zoom slider, void-fill colour picker all wired to settings. Monitor detection deferred to showEvent (needs live QApplication). Version 1.0.09. NEXT: 2.6 renderer — teach render.py to compose per-monitor / spanned output, honour zoom + focal, paint void fill; teach wallpaper.py to apply spanned or per-monitor. Also 2.4/2.5 tail: position spinboxes, view list, per-monitor independent editing, void-fill image chooser.
- Session 4 (2026-07-11): Widened Edit City dialog (500→660 min, 720x720 default) to fit its 5-tab strip. Phase 2.6 renderer done: render() accepts monitor_layout + monitors_mode + map_zoom + void_fill_color/image; _compose_multi_monitor() places rendered map onto virtual-desktop canvas with void fill (fast path when map fully covers). Verified: mirror back-compat, span mode with diagonal layout, zoom<100% shows void, zoom>100% covers. GUI: _current_resolution() returns virtual desktop size in span/independent modes so preview matches. RenderWorker takes monitor_layout snapshot (main-thread-captured, no QScreen from worker). Version 1.0.10. NEXT: 2.4/2.5 tail (position spinboxes + view list + void-fill image chooser + per-monitor independent editing), then wallpaper.py DE-specific "spanned" apply option.
- Session 5 (2026-07-11): Phase 2 CORE COMPLETE. 2.4 tail: position spinboxes (Map position X/Y in px, auto-center button) added to Displays tab, persisted per-monitor. 2.5 tail: image chooser (QFileDialog), clear-image button, "using colour" / "using image: X" status label. wallpaper.py set_wallpaper() gained spanned=True flag: sets picture-options=spanned on GNOME/Cinnamon/MATE, --no-xinerama on feh, fit mode on swaybg; worker auto-enables spanned when mode is span/independent. Version 1.0.11. Phase 2 remaining: true per-monitor independent editing (independent currently falls back to span composition), multiple "views" like EarthView's View 1/2 randomization. Both are nice-to-have; nothing blocks daily use.
- Session 6 (2026-07-11): Phase 2 FULLY COMPLETE (2.7). True per-monitor independent rendering: refactored render.py into _render_map_image (single map at any size) + _render_monitor_view (single monitor with its own zoom/focal/void, cropping when zoom>1, per-monitor padding when zoom<1) + independent-mode branch in render() that composes each monitor's render onto virtual-desktop canvas at its true position; gaps between monitors use global void fill. UI: monitor selector combo appears only in independent mode; _active_monitor_index() helper routes all Displays-tab controls (zoom, position, focal, void fill) through the selected monitor's config; switching the selector reloads control values from that monitor. Verified: per-monitor isolation (mon 0 = 150% zoom + focal (0,0), mon 1 = 75% zoom + focal (120,45)), diagonal gap magenta fill, control-value reload on selector change. Version 1.0.12.
- Session 7 (2026-07-11): User reported the mode dropdown only showed "Mirror" on their Windows setup. Code had all 3 items - display bug. Applied belt-and-braces fix: setMaxVisibleItems(10), setMinimumContentsLength(38), and explicit QListView() as the popup view (avoids QScrollArea viewport clip inheritance on some Windows styles). Also renamed labels to clearer names the user actually expected: "Mirror", "Stretch", "Custom per-monitor" (data values unchanged - still "mirror"/"span"/"independent" on the wire). Version 1.0.13.
- Session 8 (2026-07-11): CRASH FIX. App is Linux-only (Windows references in session 7 were a mistaken tangent - disregard). v1.0.13's crash-on-load was the bare QListView() set as the mode combo's popup view: PySide6 lets the Python wrapper be garbage-collected while Qt still holds the C++ pointer -> segfault under real xcb (invisible under offscreen platform, which is why earlier tests missed it). REMOVED the setView(QListView()) call; the setMaxVisibleItems(10) + setMinimumContentsLength(38) setters fix the one-row-popup display bug on their own. Added a global crash logger (gui.py _install_crash_logger): sys.excepthook writes full tracebacks to ~/.config/earthwall/earthwall_crash.log + shows a QMessageBox, so future crashes are diagnosable instead of silent. Hardened detect_layout() against zero-size/phantom screens (common mid-"Extend displays" toggle) and null primaryScreen. Wrapped both display-widget paintEvents in try/except (a raised exception in paintEvent can hard-crash Qt). Verified the full load + popup + extended-desktop + per-monitor-edit + focal-drag + repaint path under REAL xcb (xvfb + libxcb-cursor0) with zero crashes and empty crash log. Version 1.0.14.
  NOTE for future sessions: wallpaper.py is Linux-DE only (gnome/kde/cinnamon/xfce/mate/sway/feh) - no Windows/macOS paths, and that's correct/intended.
- Session 9 (2026-07-11): Phase 3 GUI redesign complete (see Phase 3 section). Biggest win: fixed the long-standing bug where Displays-tab zoom/X/Y never affected the actual desktop (only the preview) - _render_layout() now engages the placement render path for any non-default zoom/offset, and render() treats mirror+layout as span. Also split Clouds & Weather into its own tab, moved the draggable focal dot to Map & View (replacing the longitude slider), added start-in-tray option, made the Displays screen-area preview taller, and fixed the inverted-zoom bug that made it wide+short below 100%. Version 1.0.15. Verified end-to-end under real xcb.
- Session 10 (2026-07-11): Phase 4 (see section). Fixed screen-area zoom so the monitor rect never resizes (only the map scales, clipped to widget) - the wide+short breakage below 100% is gone. Added LabeledSlider (slider + synced number box) and rolled it out to cloud opacity/density, twilight, night darkness, and zoom; added X/Y number boxes for the map-center dot. Refreshed README with all Phase 2-4 features. Code review: pyflakes clean, removed dead imports/locals. Version 1.0.16. Verified end-to-end under real xcb.
- Session 11 (2026-07-11): Fixed screen-area zoom mask model. User's correct mental model: grey box = monitor (a mask/viewport), red box = the WHOLE map image (always fully outlined, thumbnail always fills it entirely). Map drawn BEHIND the screen; inside the monitor you see the map at full brightness, outside you see it dimmed (0.35) plus the red outline. Two bugs fixed: (1) was feeding the COMPOSITED render (void baked in) as the red-box thumbnail so 50% zoom showed empty space inside the box - now feeds the RAW equirectangular day-map thumbnail (_ensure_raw_map_thumb, cached per map_set) which always fills the red box; (2) rewrote _paint into 3 layers: dimmed full map across widget, then monitors (screen-colour fill + full-bright map clipped to each monitor as a mask), then red outline of whole map on top. Pixel-verified: 100% map fills screen (corner=red outline), 50% map shrinks inside screen (corner=grey screen shows through, map fills red box), 200% map extends beyond screen (dimmed map + red outline visible outside monitor). Version 1.0.17.

## Phase 5 — Natural hazard overlays (2026-07-11)
- [x] **5.1 Earthquakes** — new hazards.py fetches USGS earthquake data
      (CDN summary feeds for standard buckets, FDSN query for arbitrary
      magnitude). GUI (Clouds & Weather tab): show-earthquakes checkbox,
      min-magnitude spinbox (0-9), time-window combo (hour/day/week/month).
      Drawn as magnitude-scaled circles, colour ramp yellow->orange->red->
      magenta. Cached 5min, last-known-good, backoff, never blocks render.
- [x] **5.2 Hurricanes** — hazards.py fetches NOAA NHC CurrentStorms.json;
      GUI checkbox. Drawn as category-coloured spiral glyph at storm centre
      + name/category label + forecast track polyline (antimeridian-split)
      where geometry available. Liberal field parsing (feed shape varies).
      Cached 30min, same robustness.
- [x] **5.3 Render integration** — _draw_hazards() layers above clouds,
      below city markers; threaded through all 3 render branches (mirror/
      span/independent). Worker fetches hazard data best-effort. Every
      hazard record wrapped so malformed data can't break a render.
- [x] **5.4 README + attribution** — documented both overlays; added
      hazards.py to module list; USGS + NOAA NHC public-domain attribution.
- Session 12 (2026-07-11): Phase 5 complete. Earthquake + hurricane overlays. Sources: USGS (earthquakes, free no-auth GeoJSON - summary feeds + FDSN query), NOAA NHC (hurricanes, CurrentStorms.json). New hazards.py mirrors clouds.py robustness (disk cache, last-known-good, backoff, atomic writes, never raises into renderer). Coords: USGS is [lon,lat,depth] epoch-ms; NHC coords parsed from both numeric and "23.4N"/"81.2W" string forms. Rendering: magnitude-scaled colour-ramped quake circles, category-coloured cyclone spirals with tracks. Layered above clouds/below cities. GUI controls on Clouds & Weather tab. Pixel-verified quake/hurricane colours; visual demo looked clean. Version 1.0.18. Network fetch fails gracefully (sandbox has no USGS/NOAA access; real machine will fetch live).
- Session 13 (2026-07-11): Hazard display customisation. render.DEFAULT_HAZARD_STYLE + settings['hazard_style'] dict threaded through whole render pipeline (all 3 branches). Earthquakes: marker shape (circle/ring/dot/cross), colour mode (magnitude ramp / custom hex), size mult, AND optional magnitude-number label with custom text colour + size (the headline request). Hurricanes: shape (spiral/ring/dot), colour (category/custom), size, toggle name label, toggle forecast track. New render helpers _draw_marker, _draw_hazard_label, _hex_rgba. GUI: full display-options rows on Clouds & Weather tab under each hazard, with QColorDialog swatches. _on_hazard_style_changed / _pick_hazard_color / _load_hazard_style_widgets / _refresh_hazard_swatches handlers. Pixel-verified magnitude number (green text detected), custom colours, all shapes; visual demo of magnitude labels looked clean. Version 1.0.19.
