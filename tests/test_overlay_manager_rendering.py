"""Overlay manager regressions for clean source replacement."""

from __future__ import annotations

import numpy as np

from app.config import AppConfig
from app.models import CaptureInfo, TextRegion
from ui import overlay_manager as overlay_module
from ui.overlay_manager import OverlayManager
from utils.dpi_utils import MonitorInfo


class _Signal:
    def connect(self, _callback) -> None:
        pass


class _Window:
    def __init__(self, _config) -> None:
        self.close_requested = _Signal()
        self.blocks = []

    def setGeometry(self, *_geometry) -> None:
        pass

    def set_blocks(self, blocks) -> None:
        self.blocks = blocks

    def set_edit_mode(self, _enabled: bool) -> None:
        pass

    def show_fade(self) -> None:
        pass

    def raise_(self) -> None:
        pass

    def hide(self) -> None:
        pass


def _manager(tmp_path, monkeypatch) -> OverlayManager:
    monkeypatch.setattr(overlay_module, "TranslationOverlayWindow", _Window)
    manager = OverlayManager(AppConfig(tmp_path / "config.json"))
    manager.set_monitor_map([MonitorInfo(0, (0, 0, 200, 100), (0, 0), 1.0)])
    return manager


def _capture() -> CaptureInfo:
    return CaptureInfo(
        image=np.zeros((100, 200, 3), dtype=np.uint8),
        bbox=(0, 0, 200, 100),
        monitor_indices=[0],
        mode="fullscreen",
    )


def test_unchanged_translation_is_not_repainted(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    regions = [
        TextRegion(
            text="unchanged",
            translated_text="unchanged",
            x=10,
            y=10,
            width=60,
            height=20,
        ),
        TextRegion(
            text="Settings",
            translated_text="设置",
            x=80,
            y=10,
            width=60,
            height=20,
        ),
    ]

    assert manager.show_regions(_capture(), regions)
    blocks = manager._windows[0].blocks
    assert [block.text for block in blocks] == ["设置"]


def test_all_unchanged_results_leave_the_original_screen_clean(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    regions = [
        TextRegion(
            text="rate limited",
            translated_text="rate limited",
            x=10,
            y=10,
            width=80,
            height=20,
        )
    ]

    assert not manager.show_regions(_capture(), regions)
    assert manager._windows == {}
    assert not manager.is_visible()
