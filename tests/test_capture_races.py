"""Regression coverage for capture-session races and stale workers."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.application import Application, _CaptureSession
from app.config import AppConfig
from ui.tray_icon import TrayIcon


class _Window:
    def __init__(self) -> None:
        self.show_calls = 0

    def showNormal(self) -> None:
        self.show_calls += 1

    def raise_(self) -> None:
        pass

    def activateWindow(self) -> None:
        pass


class _Controller:
    def __init__(self) -> None:
        self.window = _Window()

    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


def test_tray_activation_cannot_reshow_main_window_while_capture_is_busy():
    app = QApplication.instance() or QApplication([])
    controller = _Controller()
    tray = TrayIcon(controller)
    tray.set_busy(True)

    tray._on_activated(tray.ActivationReason.Trigger)

    assert controller.window.show_calls == 0
    tray.deleteLater()
    app.processEvents()


def test_stale_worker_result_is_ignored(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("SCREEN_TRANSLATOR_CONFIG", str(tmp_path / "config.json"))
    controller = Application(app)
    controller.config = AppConfig(tmp_path / "config.json")
    controller._capture_session = _CaptureSession("region", False, False, False)
    active_worker = object()
    controller.worker = active_worker

    class _Overlay:
        def clear_all(self):
            raise AssertionError("stale result must not touch the active overlay")

    controller.overlay_manager = _Overlay()
    monkeypatch.setattr(controller, "sender", lambda: object())

    controller.on_pipeline_result({"capture": None, "regions": []})

    assert controller.worker is active_worker
    controller.worker = None
    controller.floating_status.hide_immediate()
    controller.floating_status.deleteLater()
    app.processEvents()


def test_monitor_refresh_updates_overlay_manager(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("SCREEN_TRANSLATOR_CONFIG", str(tmp_path / "config.json"))
    controller = Application(app)
    controller.config = AppConfig(tmp_path / "config.json")
    expected = [object()]
    observed = []

    class _Overlay:
        def set_monitor_map(self, mapping):
            observed.append(mapping)

    controller.overlay_manager = _Overlay()
    monkeypatch.setattr(controller, "_build_monitor_map", lambda: setattr(controller, "monitor_map", expected))

    controller.refresh_monitor_map()

    assert observed == [expected]
    controller.floating_status.hide_immediate()
    controller.floating_status.deleteLater()
    app.processEvents()
