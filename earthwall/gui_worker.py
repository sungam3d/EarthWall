"""
Rendering happens on a background QThread so the settings window and tray
stay responsive - a 4K render with an unsharp mask pass and network fetches
for cloud and weather data can take a second or two, enough to make a GUI
feel janky if done on the main thread.
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QThread, Signal

from . import clouds as clouds_module
from . import weather as weather_module
from .render import render
from .wallpaper import set_wallpaper


class RenderWorker(QThread):
    finished_ok = Signal(str)   # emits the output path on success
    finished_err = Signal(str)  # emits an error message on failure

    def __init__(self, settings: dict, cities: list[dict], output_path: str,
                 width: int, height: int, apply_wallpaper: bool = True):
        super().__init__()
        self.settings = settings
        self.cities = cities
        self.output_path = output_path
        self.width = width
        self.height = height
        self.apply_wallpaper = apply_wallpaper

    def _gather_weather(self) -> dict:
        """Fetch weather for any city with show_weather set. Uses the
        weather module's per-city cache + backoff, so this is very cheap
        on the steady-state case (all cached, no network); expensive only
        on the first render after startup or after a long pause."""
        result = {}
        temp_units = self.settings.get("temp_units", "C")
        for i, city in enumerate(self.cities):
            if not city.get("show_weather"):
                continue
            reading = weather_module.get_weather(city["lat"], city["lon"])
            if reading is not None:
                result[i] = reading
        return result

    def run(self) -> None:
        try:
            cloud_layer = None
            if self.settings.get("live_clouds"):
                cloud_layer = clouds_module.get_cloud_layer()

            weather_by_city = self._gather_weather()

            render(
                self.output_path,
                self.width,
                self.height,
                self.cities,
                when=datetime.now().astimezone(),
                map_id=self.settings.get("map_set", "blue_marble_july"),
                center_lon=self.settings.get("center_lon", 0.0),
                twilight_width_deg=self.settings.get("twilight_width_deg", 7.0),
                night_darkness=self.settings.get("night_darkness", 0.85),
                cloud_layer=cloud_layer,
                cloud_opacity=self.settings.get("cloud_opacity", 0.35),
                cloud_density=self.settings.get("cloud_density", 1.0),
                night_view=self.settings.get("night_view", True),
                temp_units=self.settings.get("temp_units", "C"),
                weather_by_city=weather_by_city,
            )

            if self.apply_wallpaper:
                set_wallpaper(self.output_path)

            self.finished_ok.emit(self.output_path)
        except Exception as e:  # noqa: BLE001 - surface any failure to the GUI
            self.finished_err.emit(str(e))
