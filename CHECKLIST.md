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

## Phase 2 — Multi-monitor support (big; do in sub-steps, one per session if needed)
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
      monitor_configs["0"]["zoom"]; position spinboxes (Map position X/Y)
      pin exact placement; Auto-center button resets to centred position
      at current zoom. Per-monitor placement in independent mode still to
      come (currently independent falls back to span).**
- [x] **2.5 Void fill** — when zoom/pan leaves screen area uncovered: custom
      solid colour picker or background image chooser. **DONE: colour
      swatch (QColorDialog), Image… button (QFileDialog with PNG/JPG/BMP/
      WEBP filter), Clear image button; status label ("using colour" /
      "using image: <name>") shows which is active; renderer's
      _load_void_fill honours image with aspect-preserving scale+crop
      and falls back to colour on any load error.**
- [x] **2.6 Renderer changes** — render.py: compose per-monitor or spanned
      output; wallpaper.py: apply spanned image (GNOME "spanned" option) or
      per-monitor wallpapers depending on DE support. **DONE: render()
      composes spanned output honouring zoom/position/void; wallpaper.py
      set_wallpaper(spanned=True) sets picture-options="spanned" on
      GNOME/Cinnamon/MATE, --no-xinerama on feh, appropriate mode on
      swaybg; worker auto-enables spanned when mode is span/independent.**

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
