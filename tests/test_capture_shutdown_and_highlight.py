"""Shutdown and moving-target confirmation regressions."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

import app.application as application_module
from app.application import Application, _CaptureSession
from app.config import AppConfig
from app.models import CaptureInfo
from workers.translation_worker import PipelineTask


class _SlowOCR:
    def recognize(self, _image, lang="ch"):
        loop = QEventLoop()
        QTimer.singleShot(60, loop.quit)
        loop.exec()
        return []


def test_shutdown_waits_for_real_worker_finished_before_quitting(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("SCREEN_TRANSLATOR_CONFIG", str(tmp_path / "config.json"))
    controller = Application(app)
    capture = CaptureInfo(
        image=np.zeros((40, 80, 3), dtype=np.uint8),
        bbox=(0, 0, 80, 40),
        mode="region",
    )
    controller.worker = PipelineTask(
        capture=capture,
        ocr_engine=_SlowOCR(),
        translator=None,
        config=AppConfig(tmp_path / "config.json"),
    )
    quits = []
    monkeypatch.setattr(app, "quit", lambda: quits.append(True))
    controller.worker.start()

    controller.shutdown()

    assert controller._shutting_down
    assert controller.worker is not None
    assert quits == []
    assert controller.worker.wait(3000)
    app.processEvents()
    assert controller.worker is None
    assert quits == [True]
    controller.floating_status.deleteLater()


def test_window_movement_reconfirms_once_then_schedules_capture(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("SCREEN_TRANSLATOR_CONFIG", str(tmp_path / "config.json"))
    controller = Application(app)
    controller._capture_session = _CaptureSession("window", False, False, False, 0x1234)
    controller._highlight_window_rect = (0, 0, 100, 100)
    controller._window_highlight_retries = 0
    controller.monitor_map = []
    reconfirmed = []
    scheduled = []
    monkeypatch.setattr(controller, "_dispose_window_highlight", lambda: None)
    monkeypatch.setattr(
        application_module,
        "get_window_rect_physical",
        lambda _hwnd: (10, 10, 110, 110),
    )
    monkeypatch.setattr(controller, "_after_window_departure", lambda: reconfirmed.append(True))
    monkeypatch.setattr(
        controller,
        "_schedule_capture",
        lambda *args, **kwargs: scheduled.append((args, kwargs)),
    )

    controller._on_window_highlight_finished()
    assert reconfirmed == [True]
    assert scheduled == []
    assert controller._window_highlight_retries == 1

    controller._on_window_highlight_finished()
    assert reconfirmed == [True]
    assert scheduled and scheduled[0][0] == ("window",)
    assert scheduled[0][1]["target_hwnd"] == 0x1234
    controller.floating_status.hide_immediate()
    controller.floating_status.deleteLater()
    app.processEvents()
