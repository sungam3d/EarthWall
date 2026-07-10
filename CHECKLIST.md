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
