"""
Rendering happens on a background QThread so the settings window and tray
stay responsive - a 4K render with an unsharp mask pass and a network
fetch for cloud data can take a second or two, which is enough to make a
GUI feel janky if done on the main thread.
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QThread, Signal

from . import clouds as clouds_module
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

    def run(self) -> None:
        try:
            cloud_layer = None
            if self.settings.get("live_clouds"):
                cloud_layer = clouds_module.get_cloud_layer()

            render(
                self.output_path,
                self.width,
                self.height,
                self.cities,
                when=datetime.now().astimezone(),
                map_id=self.settings.get("map_set", "blue_marble_july"),
                center_lon=self.settings.get("center_lon", 0.0),
                twilight_width_deg=self.settings.get("twilight_width_deg", 7.0),
                cloud_layer=cloud_layer,
                cloud_opacity=self.settings.get("cloud_opacity", 0.35),
            )

            if self.apply_wallpaper:
                set_wallpaper(self.output_path)

            self.finished_ok.emit(self.output_path)
        except Exception as e:  # noqa: BLE001 - surface any failure to the GUI
            self.finished_err.emit(str(e))
