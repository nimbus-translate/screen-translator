"""Offscreen regressions for region, window, and capture-session boundaries."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

import app.application as application_module
import services.window_capture_service as window_capture
from app.application import Application
from app.config import AppConfig
from app.models import CaptureInfo
from services.screenshot_service import ScreenshotService
from ui.main_window import MainWindow
from ui.selection_overlay import SelectionOverlay
from ui.window_capture_highlight import WindowCaptureHighlight
from utils import dpi_utils


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _MouseEvent:
    def __init__(self, x: int, y: int) -> None:
        self._position = QPointF(x, y)

    @staticmethod
    def button():
        return Qt.MouseButton.LeftButton

    def position(self) -> QPointF:
        return self._position


def test_legacy_red_capture_theme_is_migrated(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        '{"capture": {"select_border_color": "#FF3B30", "select_mask_opacity": 100}}',
        encoding="utf-8",
    )

    config = AppConfig(path)

    assert config.get("capture.select_border_color") == "#2878E8"
    assert config.get("capture.select_mask_opacity") == 84
    assert config.get("appearance.accent") == "#2878E8"


def test_selection_surface_uses_explicit_appearance_accent(qapp):
    overlay = SelectionOverlay(
        QRect(0, 0, 320, 240),
        border_color="#7258D6",
    )

    assert overlay._border.name().upper() == "#7258D6"
    overlay.deleteLater()


def test_selection_surface_keeps_the_chosen_area_transparent(qapp):
    overlay = SelectionOverlay(QRect(0, 0, 320, 240), mask_opacity=84)
    overlay._origin = QPoint(40, 50)
    overlay._current = QPoint(240, 180)
    image = QImage(
        overlay.size(),
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    image.fill(Qt.GlobalColor.transparent)

    overlay.render(image)

    assert image.pixelColor(5, 5).alpha() == 84
    assert image.pixelColor(120, 100).alpha() == 0
    border = image.pixelColor(40, 100)
    assert abs(border.red() - 40) <= 2
    assert abs(border.green() - 120) <= 2
    assert abs(border.blue() - 232) <= 2
    overlay.deleteLater()


def test_capture_accent_reaches_selection_feedback_without_recoloring_neutrals(qapp):
    accent = "#7258D6"
    overlay = SelectionOverlay(
        QRect(0, 0, 320, 240),
        mask_opacity=84,
        border_color=accent,
    )
    overlay._origin = QPoint(40, 50)
    overlay._current = QPoint(240, 180)
    overlay._scan_progress = 0.0
    image = QImage(
        overlay.size(),
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    image.fill(Qt.GlobalColor.transparent)
    overlay.render(image)

    expected = QColor(accent)

    def near(color: QColor, tolerance: int = 16) -> bool:
        return (
            abs(color.red() - expected.red()) <= tolerance
            and abs(color.green() - expected.green()) <= tolerance
            and abs(color.blue() - expected.blue()) <= tolerance
        )

    assert image.pixelColor(5, 5).alpha() == 84
    assert near(image.pixelColor(40, 100))  # selection border
    assert near(image.pixelColor(54, 50))  # travelling top dash
    assert any(
        near(image.pixelColor(x, y))
        for x in range(49, 62)
        for y in range(27, 34)
    )  # size badge dash
    assert sum(
        near(image.pixelColor(x, y), tolerance=28)
        for x in range(67, 134)
        for y in range(17, 44)
    ) > 8  # size text emphasis
    badge_surface = image.pixelColor(44, 20)
    assert badge_surface.red() > 245
    assert badge_surface.green() > 245
    assert badge_surface.blue() > 240
    overlay.deleteLater()


def test_window_highlight_uses_explicit_accent_and_keeps_warm_badge(qapp):
    accent = "#168A82"
    highlight = WindowCaptureHighlight(
        QRect(0, 0, 320, 180),
        "Demo",
        accent_color=accent,
    )
    highlight._progress = 0.6
    image = QImage(
        highlight.size(),
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    image.fill(Qt.GlobalColor.transparent)
    highlight.render(image)

    expected = QColor(accent)

    def near(color: QColor, tolerance: int = 18) -> bool:
        return (
            abs(color.red() - expected.red()) <= tolerance
            and abs(color.green() - expected.green()) <= tolerance
            and abs(color.blue() - expected.blue()) <= tolerance
        )

    assert highlight._accent.name().upper() == accent
    assert near(image.pixelColor(30, 9))
    assert any(
        near(image.pixelColor(x, y))
        for x in range(22, 35)
        for y in range(25, 32)
    )
    badge_surface = image.pixelColor(40, 18)
    assert badge_surface.red() > 245
    assert badge_surface.green() > 245
    assert badge_surface.blue() > 240
    highlight.deleteLater()


def test_application_resolves_capture_accent_from_appearance(tmp_path):
    config = AppConfig(tmp_path / "config.json")
    config.set("appearance.accent", "#B66A14")
    controller = type("Controller", (), {"config": config})()

    assert Application._capture_accent(controller) == "#B66A14"


@pytest.mark.parametrize(
    ("press", "release"),
    [((10, 20), (110, 220)), ((110, 220), (10, 20))],
)
def test_selection_uses_negative_virtual_origin_and_exclusive_release_edge(
    qapp, monkeypatch, press, release
):
    monkeypatch.setenv("SCREEN_TRANSLATOR_MOTION", "reduced")
    overlay = SelectionOverlay(QRect(-1920, -100, 3840, 1080))
    selections = []
    moves = []
    overlay.selection_done.connect(selections.append)

    # Deliberately omit mouseMoveEvent: release is the authoritative final edge.
    overlay.mousePressEvent(_MouseEvent(*press))
    overlay.mouseReleaseEvent(_MouseEvent(*release))
    qapp.processEvents()

    assert moves == []
    assert selections == [(-1910, -80, -1810, 120)]
    left, top, right, bottom = selections[0]
    assert right - left == abs(release[0] - press[0]) == 100
    assert bottom - top == abs(release[1] - press[1]) == 200
    overlay.deleteLater()


def test_selection_reuses_its_animation_objects(qapp, monkeypatch):
    monkeypatch.setenv("SCREEN_TRANSLATOR_MOTION", "full")
    overlay = SelectionOverlay(QRect(0, 0, 320, 240))
    entrance = overlay._entrance
    finish_animation = overlay._finish_animation
    scan_animation = overlay._scan_animation

    overlay.show()
    qapp.processEvents()
    overlay.hide()
    overlay.show()
    qapp.processEvents()
    overlay.mousePressEvent(_MouseEvent(20, 30))
    overlay.mouseReleaseEvent(_MouseEvent(120, 130))

    assert overlay._entrance is entrance
    assert overlay._finish_animation is finish_animation
    assert overlay._scan_animation is scan_animation
    overlay.dismiss()
    overlay.deleteLater()


def test_invalid_window_never_falls_back_to_full_desktop(monkeypatch):
    service = ScreenshotService()
    screen_crops = []
    monkeypatch.setattr(window_capture, "is_window_capturable", lambda _hwnd: False)
    monkeypatch.setattr(
        service,
        "capture_bbox",
        lambda bbox: screen_crops.append(bbox),
    )

    with pytest.raises(window_capture.WindowCaptureError):
        service.capture_window(0x1234)

    assert screen_crops == []


def test_window_outside_virtual_desktop_is_rejected_without_screen_crop(monkeypatch):
    service = ScreenshotService()
    screen_crops = []
    native_captures = []
    monkeypatch.setattr(window_capture, "is_window_capturable", lambda _hwnd: True)
    monkeypatch.setattr(
        window_capture,
        "get_window_rect_physical",
        lambda _hwnd: (300, 300, 500, 500),
    )
    monkeypatch.setattr(
        window_capture,
        "capture_window_bgr",
        lambda hwnd: native_captures.append(hwnd),
    )
    monkeypatch.setattr(service, "virtual_screen_bbox", lambda: (0, 0, 100, 100))
    monkeypatch.setattr(
        service,
        "capture_bbox",
        lambda bbox: screen_crops.append(bbox),
    )

    with pytest.raises(window_capture.WindowCaptureError):
        service.capture_window(0x5678)

    assert native_captures == []
    assert screen_crops == []


def test_blank_native_window_capture_falls_back_only_to_validated_window_rect(monkeypatch):
    service = ScreenshotService()
    target = (10, 20, 70, 80)
    screen_crops = []
    monkeypatch.setattr(window_capture, "is_window_capturable", lambda _hwnd: True)
    monkeypatch.setattr(window_capture, "get_window_rect_physical", lambda _hwnd: target)
    monkeypatch.setattr(
        window_capture,
        "capture_window_bgr",
        lambda _hwnd: (np.zeros((60, 60, 3), dtype=np.uint8), target),
    )
    monkeypatch.setattr(service, "virtual_screen_bbox", lambda: (0, 0, 100, 100))

    def screen_crop(bbox):
        screen_crops.append(bbox)
        return _capture_info("region", bbox)

    monkeypatch.setattr(service, "capture_bbox", screen_crop)

    capture = service.capture_window(0x9876)

    assert screen_crops == [target]
    assert capture.bbox == target
    assert capture.mode == "window"


def test_window_fallback_revalidates_a_moved_target(monkeypatch):
    service = ScreenshotService()
    first = (10, 20, 70, 80)
    moved = (25, 35, 85, 95)
    rects = iter((first, moved))
    screen_crops = []
    monkeypatch.setattr(window_capture, "is_window_capturable", lambda _hwnd: True)
    monkeypatch.setattr(
        window_capture,
        "get_window_rect_physical",
        lambda _hwnd: next(rects),
    )
    monkeypatch.setattr(
        window_capture,
        "capture_window_bgr",
        lambda _hwnd: (_ for _ in ()).throw(window_capture.WindowCaptureError("no native")),
    )
    monkeypatch.setattr(service, "virtual_screen_bbox", lambda: (0, 0, 100, 100))

    def screen_crop(bbox):
        screen_crops.append(bbox)
        return _capture_info("region", bbox)

    monkeypatch.setattr(service, "capture_bbox", screen_crop)

    capture = service.capture_window(0x2468)

    assert screen_crops == [moved]
    assert capture.bbox == moved
    assert capture.mode == "window"


class _Screen:
    def __init__(self, geometry: tuple[int, int, int, int], dpr: float) -> None:
        self._geometry = QRect(*geometry)
        self._dpr = dpr

    def geometry(self) -> QRect:
        return self._geometry

    def devicePixelRatio(self) -> float:
        return self._dpr


def _mixed_dpi_monitors() -> list[dpi_utils.MonitorInfo]:
    return [
        dpi_utils.MonitorInfo(0, (0, 0, 1920, 1080), (0, 0), 1.0),
        dpi_utils.MonitorInfo(1, (1920, 0, 3840, 1440), (1920, 0), 1.5),
    ]


def test_monitor_mapping_is_unique_when_physical_order_is_shuffled_and_dpr_mixed():
    screens = [
        _Screen((-1280, 0, 1280, 1024), 1.25),
        _Screen((0, 0, 1920, 1080), 1.0),
        _Screen((1920, 0, 1280, 960), 1.5),
    ]
    left = (-1600, 0, 0, 1280)
    primary = (0, 0, 1920, 1080)
    right = (1920, 0, 3840, 1440)

    mapping = dpi_utils.build_monitor_map(screens, [right, left, primary])

    assert [monitor.index for monitor in mapping] == [0, 1, 2]
    assert [monitor.physical for monitor in mapping] == [left, primary, right]
    assert len({monitor.physical for monitor in mapping}) == len(mapping)


def test_mixed_dpr_parts_are_not_accepted_as_one_physical_rectangle():
    parts = dpi_utils.logical_rect_to_physical_parts(
        (1800, 100, 2200, 500),
        _mixed_dpi_monitors(),
    )

    assert parts == [
        (1800, 100, 1920, 500),
        (1920, 150, 2340, 750),
    ]
    assert not dpi_utils.parts_form_rectangle(parts)


def test_application_rejects_nonrectangular_mixed_dpr_selection(qapp, monkeypatch):
    controller = Application(qapp)
    controller.monitor_map = _mixed_dpi_monitors()
    aborted = []
    monkeypatch.setattr(controller, "_dispose_selection", lambda: None)
    monkeypatch.setattr(
        controller,
        "_abort_capture",
        lambda status, **details: aborted.append((status, details)),
    )
    monkeypatch.setattr(
        controller,
        "_schedule_capture",
        lambda *_args, **_kwargs: pytest.fail("mixed-DPR selection must not be captured"),
    )

    controller._on_selection_done((1800, 100, 2200, 500))

    assert aborted
    assert aborted[0][1]["failed"] is True
    assert aborted[0][1]["error_title"] == "请在单个屏幕内框选"
    controller.floating_status.close()
    controller.floating_status.deleteLater()


class _FakeFloatingStatus:
    def __init__(self) -> None:
        self.visible = True
        self.hide_calls = 0

    def hide_immediate(self) -> None:
        self.visible = False
        self.hide_calls += 1

    def show_fade(self, *_args, **_kwargs) -> None:
        self.visible = True

    def hide_fade(self, *_args, **_kwargs) -> None:
        self.visible = False

    def set_text(self, _text: str) -> None:
        pass


class _FakeOverlayManager:
    def __init__(self, visible: bool = True) -> None:
        self.visible = visible
        self.hide_calls = 0
        self.show_calls = 0

    def is_visible(self) -> bool:
        return self.visible

    def hide_all(self, *_args, **_kwargs) -> None:
        self.visible = False
        self.hide_calls += 1

    def show_all(self) -> None:
        self.visible = True
        self.show_calls += 1

    def clear_all(self) -> None:
        self.visible = False

    def set_monitor_map(self, _monitors) -> None:
        pass


def _capture_info(mode: str, bbox=(0, 0, 8, 6)) -> CaptureInfo:
    return CaptureInfo(
        image=np.zeros((bbox[3] - bbox[1], bbox[2] - bbox[0], 3), dtype=np.uint8),
        bbox=bbox,
        monitor_indices=[0],
        mode=mode,
    )


def _capture_controller(qapp, tmp_path, monkeypatch, *, visible: bool = True):
    monkeypatch.setenv("SCREEN_TRANSLATOR_MOTION", "reduced")
    monkeypatch.setenv("SCREEN_TRANSLATOR_CONFIG", str(tmp_path / "config.json"))
    controller = Application(qapp)
    controller.config = AppConfig(tmp_path / "config.json")
    original_status = controller.floating_status
    controller.floating_status = _FakeFloatingStatus()
    controller.overlay_manager = _FakeOverlayManager(visible=True)
    controller.window = MainWindow(controller)
    controller.window.show()
    qapp.processEvents()
    if not visible:
        controller.window.hide()
        qapp.processEvents()
    return controller, original_status


def _dispose_capture_controller(controller, original_status, qapp) -> None:
    controller._capture_timer.stop()
    controller._selection_timer.stop()
    if controller.window is not None:
        controller.window.hide()
        controller.window.deleteLater()
    original_status.hide_immediate()
    original_status.close()
    original_status.deleteLater()
    qapp.processEvents()


def test_actual_screenshot_runs_after_main_status_and_overlay_are_hidden(
    qapp, tmp_path, monkeypatch
):
    controller, original_status = _capture_controller(qapp, tmp_path, monkeypatch)
    accepted = []

    class _AssertingScreenshotService:
        def capture_fullscreen(self):
            assert not controller.window.isVisible()
            assert not controller.floating_status.visible
            assert not controller.overlay_manager.visible
            return _capture_info("fullscreen")

    controller.screenshot_service = _AssertingScreenshotService()
    monkeypatch.setattr(controller, "_accept_capture", accepted.append)

    controller.start_capture("fullscreen")
    controller._capture_timer.stop()
    controller._execute_pending_capture()

    assert [capture.mode for capture in accepted] == ["fullscreen"]
    assert controller.floating_status.hide_calls >= 2
    assert controller.overlay_manager.hide_calls >= 1
    controller._abort_capture("test cleanup")
    _dispose_capture_controller(controller, original_status, qapp)


def test_region_cancel_restores_visible_main_window_and_previous_overlay(
    qapp, tmp_path, monkeypatch
):
    controller, original_status = _capture_controller(qapp, tmp_path, monkeypatch)
    monkeypatch.setattr(controller, "refresh_monitor_map", lambda: None)

    controller.start_region_capture()
    controller._selection_timer.stop()
    assert not controller.window.isVisible()
    assert not controller.overlay_manager.visible

    controller._on_selection_cancelled()
    qapp.processEvents()

    assert controller.window.isVisible()
    assert controller.overlay_manager.visible
    assert not controller._busy
    assert controller._capture_session is None
    _dispose_capture_controller(controller, original_status, qapp)


def test_capture_started_from_tray_does_not_show_hidden_main_window(
    qapp, tmp_path, monkeypatch
):
    controller, original_status = _capture_controller(
        qapp, tmp_path, monkeypatch, visible=False
    )
    monkeypatch.setattr(controller, "refresh_monitor_map", lambda: None)

    controller.start_region_capture()
    controller._selection_timer.stop()
    controller._on_selection_cancelled()
    qapp.processEvents()

    assert not controller.window.isVisible()
    assert controller.overlay_manager.visible
    assert not controller._busy
    _dispose_capture_controller(controller, original_status, qapp)


def test_error_dialog_runs_while_busy_and_before_old_overlay_is_restored(
    qapp, tmp_path, monkeypatch
):
    controller, original_status = _capture_controller(qapp, tmp_path, monkeypatch)
    controller._begin_capture_session("fullscreen", lambda: None)
    observations = []

    def inspect_dialog(title, message):
        observations.append(
            (title, message, controller._busy, controller.overlay_manager.visible)
        )

    monkeypatch.setattr(controller, "show_error", inspect_dialog)
    controller._abort_capture(
        "boom",
        failed=True,
        error_title="截图失败",
        error_message="boom",
    )

    assert observations == [("截图失败", "boom", True, False)]
    assert controller.overlay_manager.visible
    assert not controller._busy
    _dispose_capture_controller(controller, original_status, qapp)


def test_refresh_recaptures_using_the_original_capture_mode(qapp, tmp_path, monkeypatch):
    controller, original_status = _capture_controller(
        qapp, tmp_path, monkeypatch, visible=False
    )
    calls = []
    accepted = []
    region_bbox = (10, 20, 30, 40)

    class _ModeRecordingScreenshotService:
        def capture_window(self, hwnd):
            calls.append(("window", hwnd))
            return _capture_info("window")

        def capture_fullscreen(self):
            calls.append(("fullscreen",))
            return _capture_info("fullscreen")

        def capture_bbox(self, bbox):
            calls.append(("region", bbox))
            return _capture_info("region", bbox)

    controller.screenshot_service = _ModeRecordingScreenshotService()
    monkeypatch.setattr(controller, "_accept_capture", accepted.append)
    monkeypatch.setattr(application_module, "is_window_capturable", lambda _hwnd: True)

    for mode in ("window", "fullscreen", "region"):
        controller._last_capture = _capture_info(mode, region_bbox)
        controller._last_window_hwnd = 0x123456
        controller.refresh()
        controller._capture_timer.stop()
        controller._execute_pending_capture()
        controller._abort_capture("test cleanup")

    assert calls == [
        ("window", 0x123456),
        ("fullscreen",),
        ("region", region_bbox),
    ]
    assert [capture.mode for capture in accepted] == ["window", "fullscreen", "region"]
    assert not controller.window.isVisible()
    _dispose_capture_controller(controller, original_status, qapp)
