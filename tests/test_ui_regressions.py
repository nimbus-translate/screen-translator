"""Offscreen regressions for embedded settings and animation lifecycles."""

from __future__ import annotations

import copy
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import (
    QAbstractAnimation,
    QCoreApplication,
    QEvent,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
)
from PySide6.QtGui import QColor, QImage, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QMessageBox,
    QPushButton,
    QWidget,
)

from app.application import Application
from app.config import AppConfig
from app.hotkeys import normalize_hotkey
from ui.appearance import resolve_tokens
from ui.floating_status import FloatingStatus
from ui.main_window import MainWindow, NativePageDeck
from ui.motion import BASE, FAST, motion_duration
from ui.settings_dialog import SettingsDialog
from ui.settings_transition import SettingsTransitionGuard
from ui.style import apply_style, build_stylesheet
from ui.translation_overlay import Block, TranslationOverlayWindow


@pytest.fixture(scope="module")
def qapp():
    instance = QApplication.instance() or QApplication([])
    apply_style(instance)
    yield instance


def _wait_until(qapp, predicate, *, timeout_ms: int = 1400) -> None:
    """Drive the offscreen event loop until a transition reaches a stable state."""
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        QTest.qWait(5)
    qapp.processEvents()
    assert predicate(), "timed out waiting for the UI transition to settle"


def _assert_settings_deck_frame(qapp, window, page, progress: float) -> int:
    """Assert the two real pages meet at one exact integer seam."""
    window._set_settings_progress(progress)
    qapp.processEvents()

    deck = window._pages
    bounds = deck.rect()
    width = bounds.width()
    seam = round(width * (1.0 - progress))
    assert window._settings_progress == pytest.approx(progress)
    assert window.windowOpacity() == pytest.approx(1.0)
    assert deck.isTransitioning()
    assert window._settings_guard.geometry() == window.contentsRect()
    assert window._settings_guard.isVisible()
    assert window._home_page.parentWidget() is deck
    assert page.parentWidget() is deck
    assert window._home_page.geometry() == QRect(
        seam - width, 0, width, bounds.height()
    )
    assert page.geometry() == QRect(seam, 0, width, bounds.height())
    assert window._home_page.geometry().right() + 1 == page.geometry().left()
    assert window._home_page.isVisibleTo(window)
    assert page.isVisibleTo(window)
    destination = page if progress >= 0.5 else window._home_page
    assert deck.currentWidget() is destination
    return seam


class _Controller:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def start_region_capture(self) -> None:
        pass

    def start_capture(self, _mode: str) -> None:
        pass

    def open_settings(self) -> None:
        pass

    def toggle_overlay(self) -> None:
        pass

    def set_edit_mode(self, _enabled: bool) -> None:
        pass

    def refresh(self) -> None:
        pass

    def apply_runtime_selection(self, **_values) -> None:
        pass


@pytest.mark.parametrize("size", [(980, 580), (900, 540)])
def test_settings_footer_stays_inside_fixed_window(qapp, tmp_path, size):
    config = AppConfig(tmp_path / "config.json")
    window = MainWindow(_Controller(config))
    window.resize(*size)
    window.show()
    dialog = SettingsDialog(config, parent=window)
    dialog.setWindowFlags(Qt.WindowType.Widget)
    window.show_settings_page(dialog)
    dialog.show()
    qapp.processEvents()

    assert window.size() == QSize(*size)
    assert dialog.geometry() == window.contentsRect()
    for button in (dialog.btn_cancel, dialog.btn_save):
        button_rect = QRect(button.mapTo(dialog, QPoint(0, 0)), button.size())
        assert dialog.rect().contains(button_rect)
        assert button.visibleRegion().boundingRect() == button.rect()

    window.hide()
    dialog.deleteLater()
    window.deleteLater()


def test_open_settings_reuses_active_page(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("SCREEN_TRANSLATOR_CONFIG", str(tmp_path / "config.json"))
    controller = Application(qapp)
    window = MainWindow(controller)
    controller.window = window

    controller.open_settings()
    first = window._pages.currentWidget()
    assert window._pages.count() == 2

    controller.open_settings()
    assert window._pages.count() == 2
    assert window._pages.currentWidget() is first

    first.reject()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()
    assert window._pages.count() == 1

    controller.open_settings()
    assert window._pages.count() == 2
    assert window._pages.currentWidget() is not first

    controller._settings_page.reject()
    controller.floating_status.close()
    window.hide()
    window.deleteLater()


def test_settings_button_uses_plain_qpushbutton_without_custom_decoration(
    qapp, tmp_path
):
    window = MainWindow(_Controller(AppConfig(tmp_path / "config.json")))

    assert type(window.btn_settings) is QPushButton
    assert type(window.btn_settings).paintEvent is QPushButton.paintEvent
    assert not hasattr(window.btn_settings, "set_portal_progress")
    assert not hasattr(window.btn_settings, "_portal_progress")
    window.deleteLater()


def test_settings_outer_transition_full_settles_and_preserves_inner_curtain(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setenv("SCREEN_TRANSLATOR_MOTION", "full")
    apply_style(qapp)
    window = MainWindow(_Controller(AppConfig(tmp_path / "config.json")))
    window.resize(980, 580)
    window.show()
    dialog = SettingsDialog(window.controller.config, parent=window)
    dialog.setWindowFlags(Qt.WindowType.Widget)
    qapp.processEvents()
    window._visibility_animation.stop()
    window.setWindowOpacity(1.0)

    outer_animation = window._settings_transition_animation
    curtain_in = dialog._page_sweep_in
    curtain_out = dialog._page_sweep_out
    nav_animation = dialog._nav_animation

    window.show_settings_page(dialog)

    assert window._settings_transition_state == "entering"
    assert outer_animation.state() == QAbstractAnimation.State.Running
    assert outer_animation.duration() == motion_duration(
        BASE + FAST, large_surface=True
    )
    assert dialog.graphicsEffect() is None
    assert window._pages.isTransitioning()
    assert window._settings_guard.isVisible()
    assert dialog._page_sweep_in is curtain_in
    assert dialog._page_sweep_out is curtain_out
    assert dialog._nav_animation is nav_animation
    assert not dialog._page_transition_active
    assert not dialog._page_sweep.isVisible()

    outer_animation.stop()
    entering_frames = {
        progress: _assert_settings_deck_frame(qapp, window, dialog, progress)
        for progress in (0.25, 0.5, 0.75)
    }
    assert entering_frames == {
        0.25: 735,
        0.5: 490,
        0.75: 245,
    }

    window._set_settings_progress(0.37)
    window.resize(913, 547)
    qapp.processEvents()
    resized_width = window._pages.width()
    resized_seam = round(resized_width * 0.63)
    assert window._home_page.geometry() == QRect(
        resized_seam - resized_width, 0, resized_width, window._pages.height()
    )
    assert dialog.geometry() == QRect(
        resized_seam, 0, resized_width, window._pages.height()
    )
    assert window._home_page.geometry().right() + 1 == dialog.geometry().left()
    before_theme_frame = (window._home_page.geometry(), dialog.geometry())
    apply_style(qapp, {"accent": "#7258D6"})
    qapp.processEvents()
    assert (window._home_page.geometry(), dialog.geometry()) == before_theme_frame

    live_progress = window._settings_progress
    live_frame = (window._home_page.geometry(), dialog.geometry())
    window._animate_settings_to(1.0)
    assert window._settings_progress == pytest.approx(live_progress, abs=0.002)
    assert (window._home_page.geometry(), dialog.geometry()) == live_frame
    assert outer_animation.state() == QAbstractAnimation.State.Running
    _wait_until(qapp, lambda: window._settings_transition_state == "active")

    assert window._settings_transition_animation is outer_animation
    assert window._settings_progress == pytest.approx(1.0)
    assert window._pages.currentWidget() is dialog
    assert window._pages.count() == 2
    assert dialog.geometry() == window.contentsRect()
    assert dialog._lifecycle_progress == pytest.approx(1.0)
    assert dialog.isVisible()
    assert dialog.graphicsEffect() is None
    assert window.windowOpacity() == pytest.approx(1.0)
    assert window._home_page.geometry() == window.contentsRect()
    assert not window._pages.isTransitioning()
    assert not window._home_page.isVisible()
    assert not window._settings_guard.isVisible()

    window.resize(940, 560)
    qapp.processEvents()
    assert not window._pages.isTransitioning()
    assert dialog.geometry() == window._pages.rect()
    assert dialog.isVisible()
    assert not window._home_page.isVisible()

    host = dialog._page_sweep.parentWidget()
    handoffs = []
    dialog.pages.currentChanged.connect(
        lambda index: handoffs.append((index, dialog._page_sweep.geometry()))
    )
    dialog._select_page(1)
    qapp.processEvents()

    assert dialog._page_transition_active
    assert dialog._page_sweep.isVisible()
    assert window._settings_transition_state == "active"
    assert dialog._page_sweep_in is curtain_in
    assert dialog._page_sweep_out is curtain_out
    assert dialog._nav_animation is nav_animation

    _wait_until(qapp, lambda: not dialog._page_transition_active)

    assert handoffs == [(1, QRect(0, 0, host.width(), host.height()))]
    assert dialog.pages.currentIndex() == 1
    assert not dialog._page_sweep.isVisible()
    assert window._settings_transition_state == "active"
    assert window._settings_transition_animation is outer_animation

    window.close_settings_page(dialog)
    assert window._settings_transition_state == "exiting"
    assert outer_animation.state() == QAbstractAnimation.State.Running
    assert window._pages.isTransitioning()
    assert window._settings_guard.isVisible()

    outer_animation.stop()
    exiting_frames = {
        progress: _assert_settings_deck_frame(qapp, window, dialog, progress)
        for progress in (0.75, 0.5, 0.25)
    }
    assert exiting_frames == {0.75: 235, 0.5: 470, 0.25: 705}

    _assert_settings_deck_frame(qapp, window, dialog, 0.63)
    live_progress = window._settings_progress
    live_frame = (window._home_page.geometry(), dialog.geometry())
    window._animate_settings_to(0.0)
    assert window._settings_progress == pytest.approx(live_progress, abs=0.002)
    assert (window._home_page.geometry(), dialog.geometry()) == live_frame
    assert outer_animation.state() == QAbstractAnimation.State.Running
    _wait_until(qapp, lambda: window._settings_transition_state == "home")

    assert window._settings_progress == pytest.approx(0.0)
    assert window._pages.currentWidget() is window._home_page
    assert window._pages.count() == 1
    assert window._home_page.geometry() == window.contentsRect()
    assert window.windowOpacity() == pytest.approx(1.0)
    assert not window._pages.isTransitioning()
    assert window._home_page.isVisible()
    assert not window._settings_guard.isVisible()
    assert outer_animation.state() == QAbstractAnimation.State.Stopped
    window.resize(900, 540)
    qapp.processEvents()
    assert not window._pages.isTransitioning()
    assert window._home_page.geometry() == window._pages.rect()
    assert window._home_page.isVisible()
    window.hide()
    window.deleteLater()


def test_settings_guard_has_no_custom_paint_and_blocks_input_during_motion(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setenv("SCREEN_TRANSLATOR_MOTION", "full")
    apply_style(qapp)
    window = MainWindow(_Controller(AppConfig(tmp_path / "config.json")))
    window.show()
    dialog = SettingsDialog(window.controller.config, parent=window)
    dialog.setWindowFlags(Qt.WindowType.Widget)
    qapp.processEvents()
    window._visibility_animation.stop()
    window.setWindowOpacity(1.0)

    window.show_settings_page(dialog)

    guard = window._settings_guard
    assert type(window._pages) is NativePageDeck
    assert "paintEvent" not in NativePageDeck.__dict__
    assert NativePageDeck.paintEvent is QWidget.paintEvent
    assert type(guard) is SettingsTransitionGuard
    assert "paintEvent" not in SettingsTransitionGuard.__dict__
    assert SettingsTransitionGuard.paintEvent is QWidget.paintEvent
    assert not hasattr(guard, "curtain")
    assert guard.findChildren(QWidget) == []
    assert guard.isVisible()
    assert guard.geometry() == window.contentsRect()
    assert guard.isEnabled()
    assert not guard.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert window.childAt(window.rect().center()) is guard
    assert guard.hasFocus()
    assert window._home_page.isEnabled()
    assert dialog.isEnabled()
    for event_type in (
        QEvent.Type.MouseButtonPress,
        QEvent.Type.Wheel,
        QEvent.Type.KeyPress,
        QEvent.Type.Shortcut,
    ):
        blocked = QEvent(event_type)
        blocked.setAccepted(False)
        assert guard.event(blocked)
        assert blocked.isAccepted()

    window._settings_transition_animation.stop()
    _assert_settings_deck_frame(qapp, window, dialog, 0.5)
    assert window.windowOpacity() == pytest.approx(1.0)
    assert guard.findChildren(QWidget) == []
    assert window.childAt(window.rect().center()) is guard

    window._animate_settings_to(1.0)
    _wait_until(qapp, lambda: window._settings_transition_state == "active")

    assert not guard.isVisible()
    assert window.windowOpacity() == pytest.approx(1.0)
    assert window._home_page.isEnabled()
    assert dialog.isEnabled()

    exited = []
    window.begin_settings_exit(dialog, lambda: exited.append(True))

    assert guard.isVisible()
    assert window.childAt(window.rect().center()) is guard
    assert guard.hasFocus()
    assert window._home_page.isEnabled()
    assert dialog.isEnabled()

    _wait_until(qapp, lambda: window._settings_transition_state == "home")

    assert exited == [True]
    assert not guard.isVisible()
    assert window._home_page.isEnabled()
    assert dialog.isEnabled()
    window.remove_settings_page(dialog)
    window.hide()
    window.deleteLater()


def test_capture_settle_mid_exit_consumes_callback_once_and_keeps_one_home_page(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setenv("SCREEN_TRANSLATOR_MOTION", "full")
    apply_style(qapp)
    window = MainWindow(_Controller(AppConfig(tmp_path / "config.json")))
    window.show()
    qapp.processEvents()
    window._visibility_animation.stop()
    window.setWindowOpacity(1.0)

    dialog = SettingsDialog(window.controller.config, parent=window)
    dialog.setWindowFlags(Qt.WindowType.Widget)
    window.show_settings_page(dialog)
    _wait_until(qapp, lambda: window._settings_transition_state == "active")

    callbacks = []
    window.begin_settings_exit(
        dialog,
        lambda: callbacks.append(window._settings_transition_state),
    )
    _wait_until(
        qapp,
        lambda: (
            window._settings_transition_state == "exiting"
            and 0.1 < window._settings_progress < 0.9
        ),
    )
    assert callbacks == []
    assert window._settings_transition_animation.state() == (
        QAbstractAnimation.State.Running
    )

    window.settle_settings_transition_for_capture()
    qapp.processEvents()

    assert callbacks == ["home"]
    assert window._settings_exit_callback is None
    assert window._settings_transition_state == "home"
    assert window._settings_progress == pytest.approx(0.0)
    assert window._settings_transition_animation.state() == (
        QAbstractAnimation.State.Stopped
    )
    assert not window._pages.isTransitioning()
    assert window._pages.currentWidget() is window._home_page
    assert window._home_page.isVisibleTo(window)
    assert not dialog.isVisibleTo(window)
    assert not window._settings_guard.isVisible()

    # Repeated capture-settle calls and queued events must not consume it again.
    window.settle_settings_transition_for_capture()
    QTest.qWait(20)
    qapp.processEvents()
    assert callbacks == ["home"]

    window.resize(925, 555)
    qapp.processEvents()
    assert not window._pages.isTransitioning()
    assert window._pages.currentWidget() is window._home_page
    assert window._home_page.geometry() == window._pages.rect()
    assert window._home_page.isVisibleTo(window)
    assert not dialog.isVisibleTo(window)

    window.remove_settings_page(dialog)
    window.hide()
    window.deleteLater()


def test_settings_exit_restores_the_home_control_that_had_focus(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setenv("SCREEN_TRANSLATOR_MOTION", "full")
    apply_style(qapp)
    window = MainWindow(_Controller(AppConfig(tmp_path / "config.json")))
    window.show()
    window.activateWindow()
    window.combo_source.setFocus()
    qapp.processEvents()
    assert window.focusWidget() is window.combo_source

    dialog = SettingsDialog(window.controller.config, parent=window)
    dialog.setWindowFlags(Qt.WindowType.Widget)
    window.show_settings_page(dialog)
    _wait_until(qapp, lambda: window._settings_transition_state == "active")

    assert window.focusWidget() is dialog.nav_buttons[dialog.pages.currentIndex()]

    window.close_settings_page(dialog)
    _wait_until(qapp, lambda: window._settings_transition_state == "home")

    assert window._pages.currentWidget() is window._home_page
    assert window.focusWidget() is window.combo_source
    assert window.combo_source.hasFocus()
    window.hide()
    window.deleteLater()


def test_cancelled_settings_exit_reverses_live_progress_and_reuses_objects(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setenv("SCREEN_TRANSLATOR_MOTION", "full")
    monkeypatch.setenv("SCREEN_TRANSLATOR_CONFIG", str(tmp_path / "config.json"))
    apply_style(qapp)
    controller = Application(qapp)
    window = MainWindow(controller)
    controller.window = window
    window.show()
    qapp.processEvents()

    controller.open_settings()
    page = controller._settings_page
    assert page is not None
    outer_animation = window._settings_transition_animation
    curtain_in = page._page_sweep_in
    curtain_out = page._page_sweep_out
    nav_animation = page._nav_animation
    _wait_until(qapp, lambda: window._settings_transition_state == "active")
    assert page.graphicsEffect() is None

    page.reject()
    assert page.exit_pending
    assert page.pending_exit_result == 0
    assert window._settings_transition_state == "exiting"
    _wait_until(
        qapp,
        lambda: (
            window._settings_transition_state == "exiting"
            and 0.10 < window._settings_progress < 0.90
        ),
    )
    live_progress = window._settings_progress
    live_home_geometry = window._home_page.geometry()
    live_page_geometry = page.geometry()
    live_clock = outer_animation.currentTime()
    live_duration = outer_animation.duration()
    live_destination = page if live_progress >= 0.5 else window._home_page
    assert window.windowOpacity() == pytest.approx(1.0)
    assert outer_animation.state() == QAbstractAnimation.State.Running
    assert outer_animation.direction() == QAbstractAnimation.Direction.Backward
    assert window._pages.isTransitioning()
    assert window._pages.currentWidget() is live_destination
    assert window._home_page.isVisibleTo(window)
    assert page.isVisibleTo(window)

    controller.open_settings()

    assert controller._settings_page is page
    assert not page.exit_pending
    assert page.pending_exit_result == -1
    assert window._settings_transition_state == "entering"
    assert window._settings_transition_animation is outer_animation
    assert outer_animation.state() == QAbstractAnimation.State.Running
    assert outer_animation.duration() == live_duration
    assert outer_animation.currentTime() == live_clock
    assert outer_animation.direction() == QAbstractAnimation.Direction.Forward
    reversed_progress = window._settings_progress
    assert reversed_progress == pytest.approx(live_progress, abs=0.03)
    assert window.windowOpacity() == pytest.approx(1.0)
    assert window._home_page.geometry() == live_home_geometry
    assert page.geometry() == live_page_geometry
    assert window._pages.currentWidget() is live_destination
    assert page.graphicsEffect() is None
    assert not hasattr(window._settings_guard, "curtain")
    assert window._pages.isTransitioning()
    assert window._settings_guard.isVisible()
    _wait_until(
        qapp,
        lambda: (
            window._settings_transition_state == "active"
            or window._settings_progress >= reversed_progress + 0.02
        ),
    )
    assert window._settings_progress >= reversed_progress - 0.03
    assert window.windowOpacity() == pytest.approx(1.0)
    _assert_settings_deck_frame(
        qapp, window, page, window._settings_progress
    )
    destination = (
        page if window._settings_progress >= 0.5 else window._home_page
    )
    assert window._pages.currentWidget() is destination
    assert window._home_page.isVisibleTo(window)
    assert page.isVisibleTo(window)
    assert page.graphicsEffect() is None
    assert page._page_sweep_in is curtain_in
    assert page._page_sweep_out is curtain_out
    assert page._nav_animation is nav_animation

    _wait_until(qapp, lambda: window._settings_transition_state == "active")

    assert window._settings_progress == pytest.approx(1.0)
    assert window._pages.currentWidget() is page
    assert window._pages.count() == 2
    assert page.geometry() == window.contentsRect()
    assert page.isVisible()
    assert page.btn_cancel.isEnabled()
    assert page.btn_save.isEnabled()
    assert page.geometry() == window.contentsRect()
    assert page.graphicsEffect() is None
    assert window.windowOpacity() == pytest.approx(1.0)
    assert not window._pages.isTransitioning()
    assert not window._home_page.isVisible()
    assert not window._settings_guard.isVisible()
    assert outer_animation.state() == QAbstractAnimation.State.Stopped

    page.reject()
    _wait_until(qapp, lambda: controller._settings_page is None)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()
    assert window._pages.currentWidget() is window._home_page
    assert window._pages.count() == 1
    controller.floating_status.close()
    controller.floating_status.deleteLater()
    window.hide()
    window.deleteLater()


def test_open_during_saved_confirmation_waits_for_old_page_then_reopens(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setenv("SCREEN_TRANSLATOR_MOTION", "full")
    monkeypatch.setenv("SCREEN_TRANSLATOR_CONFIG", str(tmp_path / "config.json"))
    apply_style(qapp)
    controller = Application(qapp)
    monkeypatch.setattr(controller, "_apply_settings", lambda: None)
    window = MainWindow(controller)
    controller.window = window
    window.show()
    qapp.processEvents()

    controller.open_settings()
    old_page = controller._settings_page
    assert old_page is not None
    outer_animation = window._settings_transition_animation
    _wait_until(qapp, lambda: window._settings_transition_state == "active")

    old_page._commit_accept({key: "" for key in old_page.hotkey_edits})

    assert old_page.close_intent == 1
    assert not old_page.exit_pending
    assert old_page._save_close_timer.isActive()
    assert not old_page._settings_shell.isEnabled()

    controller.open_settings()

    assert controller._settings_page is old_page
    assert controller._reopen_settings_after_close
    assert window._pages.count() == 2
    assert window._pages.currentWidget() is old_page

    _wait_until(
        qapp,
        lambda: (
            controller._settings_page is not None
            and controller._settings_page is not old_page
            and window._settings_transition_state == "active"
        ),
        timeout_ms=2600,
    )

    new_page = controller._settings_page
    assert new_page is not None
    assert new_page is not old_page
    assert window._settings_transition_animation is outer_animation
    assert not controller._reopen_settings_after_close
    assert window._pages.count() == 2
    assert window._pages.currentWidget() is new_page
    assert new_page.geometry() == window.contentsRect()
    assert new_page.isVisible()
    assert not window._pages.isTransitioning()
    assert not window._home_page.isVisible()
    assert not window._settings_guard.isVisible()

    new_page.reject()
    _wait_until(qapp, lambda: controller._settings_page is None)
    controller.floating_status.close()
    controller.floating_status.deleteLater()
    window.hide()
    window.deleteLater()


def test_saved_reopen_survives_capture_busy_after_exit_is_settled(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setenv("SCREEN_TRANSLATOR_MOTION", "full")
    monkeypatch.setenv("SCREEN_TRANSLATOR_CONFIG", str(tmp_path / "config.json"))
    apply_style(qapp)
    controller = Application(qapp)
    monkeypatch.setattr(controller, "_apply_settings", lambda: None)
    window = MainWindow(controller)
    controller.window = window
    window.show()
    qapp.processEvents()

    controller.open_settings()
    old_page = controller._settings_page
    assert old_page is not None
    _wait_until(qapp, lambda: window._settings_transition_state == "active")

    old_page._commit_accept({key: "" for key in old_page.hotkey_edits})
    controller.open_settings()

    assert old_page.close_intent == 1
    assert controller._reopen_settings_after_close
    assert controller._settings_page is old_page

    reopen_attempts = []
    original_try_reopen = controller._try_reopen_settings

    def record_try_reopen() -> None:
        reopen_attempts.append(controller._busy)
        original_try_reopen()

    monkeypatch.setattr(controller, "_try_reopen_settings", record_try_reopen)

    # Deterministically advance the saved-confirmation timer into its exit.
    old_page._save_close_timer.stop()
    old_page.accept()
    _wait_until(
        qapp,
        lambda: (
            window._settings_transition_state == "exiting"
            and 0.1 < window._settings_progress < 0.9
        ),
    )

    # Capture settles the live exit synchronously, removing the old page and
    # queueing a zero-delay reopen. Busy takes ownership before that timer runs.
    window.settle_settings_transition_for_capture()
    assert controller._settings_page is None
    assert controller._reopen_settings_after_close
    assert window._pages.count() == 1
    assert window._pages.currentWidget() is window._home_page

    controller._set_busy(True)
    qapp.processEvents()

    assert reopen_attempts == [True]
    assert controller._settings_page is None
    assert controller._reopen_settings_after_close
    assert window._pages.count() == 1

    controller._set_busy(False)
    _wait_until(
        qapp,
        lambda: (
            controller._settings_page is not None
            and controller._settings_page is not old_page
            and window._settings_transition_state == "active"
        ),
    )

    new_page = controller._settings_page
    assert reopen_attempts == [True, False]
    assert new_page is not None
    assert not controller._reopen_settings_after_close
    assert window._pages.count() == 2
    assert sum(
        isinstance(window._pages.widget(index), SettingsDialog)
        for index in range(window._pages.count())
    ) == 1
    assert window._pages.indexOf(old_page) == -1
    assert window._pages.currentWidget() is new_page
    assert new_page.isVisible()

    new_page.reject()
    _wait_until(qapp, lambda: controller._settings_page is None)
    controller.floating_status.close()
    controller.floating_status.deleteLater()
    window.hide()
    window.deleteLater()


@pytest.mark.parametrize("mode", ["reduced", "eco"])
def test_settings_outer_transition_is_synchronous_without_large_surface_motion(
    qapp, tmp_path, monkeypatch, mode
):
    monkeypatch.setenv("SCREEN_TRANSLATOR_MOTION", mode)
    apply_style(qapp)
    window = MainWindow(_Controller(AppConfig(tmp_path / "config.json")))
    window.resize(900, 540)
    window.show()
    dialog = SettingsDialog(window.controller.config, parent=window)
    dialog.setWindowFlags(Qt.WindowType.Widget)
    qapp.processEvents()

    outer_animation = window._settings_transition_animation
    curtain_in = dialog._page_sweep_in
    curtain_out = dialog._page_sweep_out
    window.show_settings_page(dialog)

    assert window._settings_transition_state == "active"
    assert window._settings_progress == pytest.approx(1.0)
    assert outer_animation.state() == QAbstractAnimation.State.Stopped
    assert window._pages.currentWidget() is dialog
    assert window._pages.count() == 2
    assert dialog.geometry() == window.contentsRect()
    assert dialog._lifecycle_progress == pytest.approx(1.0)
    assert dialog.graphicsEffect() is None
    assert window.windowOpacity() == pytest.approx(1.0)
    assert not window._pages.isTransitioning()
    assert not window._settings_guard.isVisible()
    assert window._settings_guard.findChildren(QWidget) == []
    assert dialog.isVisibleTo(window)
    assert not window._home_page.isVisibleTo(window)

    dialog._select_page(1)

    assert dialog.pages.currentIndex() == 1
    assert not dialog._page_transition_active
    assert not dialog._page_sweep.isVisible()
    assert dialog._page_sweep_in is curtain_in
    assert dialog._page_sweep_out is curtain_out
    assert curtain_in.state() == QAbstractAnimation.State.Stopped
    assert curtain_out.state() == QAbstractAnimation.State.Stopped

    window.close_settings_page(dialog)

    assert window._settings_transition_state == "home"
    assert window._settings_progress == pytest.approx(0.0)
    assert outer_animation.state() == QAbstractAnimation.State.Stopped
    assert window._pages.currentWidget() is window._home_page
    assert window._pages.count() == 1
    assert window._home_page.geometry() == window.contentsRect()
    assert window.windowOpacity() == pytest.approx(1.0)
    assert not window._pages.isTransitioning()
    assert not window._settings_guard.isVisible()
    window.hide()
    window.deleteLater()


def test_hotkey_conflict_does_not_mutate_config(qapp, tmp_path, monkeypatch):
    config = AppConfig(tmp_path / "config.json")
    dialog = SettingsDialog(config)
    dialog.show()
    before = copy.deepcopy(config.data)
    dialog.chk_autostart.setChecked(not dialog.chk_autostart.isChecked())
    duplicate = dialog.hotkey_edits["capture_region"].keySequence()
    dialog.hotkey_edits["capture_fullscreen"].setKeySequence(duplicate)

    warnings = []
    saves = []
    accepted = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(
            lambda *args, **kwargs: warnings.append(args)
            or QMessageBox.StandardButton.Ok
        ),
    )
    monkeypatch.setattr(config, "save", lambda: saves.append(True))
    dialog.accepted.connect(lambda: accepted.append(True))

    dialog.btn_save.click()
    qapp.processEvents()

    assert warnings
    assert config.data == before
    assert saves == []
    assert accepted == []
    assert dialog.isVisible()
    assert dialog.btn_save.isEnabled()
    assert dialog.btn_save.text() == "保存设置"
    dialog.close()


def test_hotkeys_round_trip_and_load_legacy_pynput_values(qapp, tmp_path):
    config = AppConfig(tmp_path / "config.json")
    config.set("hotkeys.capture_region", "<ctrl>+<shift>+a")
    dialog = SettingsDialog(config)

    displayed = dialog.hotkey_edits["capture_region"].keySequence().toString(
        QKeySequence.SequenceFormat.PortableText
    )
    assert normalize_hotkey(displayed) == "<ctrl>+<shift>+a"
    values = dialog._validated_hotkeys()
    assert values is not None
    assert normalize_hotkey(values["capture_region"]) == "<ctrl>+<shift>+a"
    assert "<" not in values["capture_region"]
    dialog.deleteLater()


@pytest.mark.parametrize(
    ("hide_delay", "reshow_after"),
    [(100, 25), (0, 50)],
)
def test_new_status_show_invalidates_older_hide(qapp, hide_delay, reshow_after):
    status = FloatingStatus()
    status.show_fade("第一次")
    QTest.qWait(220)
    status.hide_fade(hide_delay)
    QTest.qWait(reshow_after)
    status.show_fade("第二次")
    QTest.qWait(400)

    assert status.isVisible()
    assert status.windowOpacity() == pytest.approx(1.0, abs=0.05)
    assert status._text == "第二次"

    status.hide_fade()
    QTest.qWait(230)
    assert not status.isVisible()
    status.deleteLater()


def test_translation_overlay_reuses_animations_and_drag_is_bounded(qapp, tmp_path):
    config = AppConfig(tmp_path / "config.json")
    overlay = TranslationOverlayWindow(config)
    overlay.resize(120, 80)
    block = Block(QRectF(0, 0, 40, 20), "hello", QColor("#FFFFFF"))
    overlay.set_blocks([block])
    fade_animation = overlay._fade_animation
    hide_animation = overlay._hide_animation

    overlay.show_fade()
    QTest.qWait(210)
    overlay.hide_fade()
    QTest.qWait(50)
    overlay.show_fade()
    QTest.qWait(220)
    assert overlay.isVisible()
    assert overlay._fade_animation is fade_animation
    assert overlay._hide_animation is hide_animation

    class _MoveEvent:
        @staticmethod
        def position() -> QPointF:
            return QPointF(500, 500)

    overlay._edit_mode = True
    overlay._drag_index = 0
    overlay._drag_offset = QPoint(0, 0)
    overlay.mouseMoveEvent(_MoveEvent())
    assert block.rect.right() <= overlay.width()
    assert block.rect.bottom() <= overlay.height()
    overlay.hide()
    overlay.deleteLater()


def test_settings_switch_preserves_the_full_page_curtain(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setenv("SCREEN_TRANSLATOR_MOTION", "full")
    dialog = SettingsDialog(AppConfig(tmp_path / "config.json"))
    dialog.resize(900, 610)
    dialog.show()
    qapp.processEvents()

    host = dialog._page_sweep.parentWidget()
    handoffs = []
    dialog.pages.currentChanged.connect(
        lambda index: handoffs.append((index, dialog._page_sweep.geometry()))
    )
    dialog._select_page(1)
    qapp.processEvents()

    assert isinstance(dialog._page_sweep, QFrame)
    assert dialog._page_sweep.isVisible()
    assert dialog._page_sweep.width() == host.width()
    assert dialog._page_sweep.height() == host.height()
    assert dialog.pages.currentIndex() == 0
    assert dialog._page_sweep.x() >= 0

    dialog._select_page(1)
    qapp.processEvents()
    assert dialog.pages.currentIndex() == 0
    assert dialog._queued_page is None

    QTest.qWait(210)
    assert dialog.pages.currentIndex() == 1
    assert handoffs == [(1, QRect(0, 0, host.width(), host.height()))]
    assert dialog._page_sweep_out.endValue().x() == -host.width()
    QTest.qWait(210)
    assert not dialog._page_sweep.isVisible()
    dialog.hide()
    dialog.deleteLater()


def test_settings_switch_queues_latest_page_and_recomputes_direction(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setenv("SCREEN_TRANSLATOR_MOTION", "full")
    dialog = SettingsDialog(AppConfig(tmp_path / "config.json"))
    dialog.resize(900, 610)
    dialog.show()
    qapp.processEvents()

    host = dialog._page_sweep.parentWidget()
    dialog._select_page(5)
    dialog._select_page(1)
    qapp.processEvents()

    assert dialog.pages.currentIndex() == 0
    assert dialog._pending_page == 5
    assert dialog._queued_page == 1

    QTest.qWait(210)
    assert dialog.pages.currentIndex() == 5
    QTest.qWait(210)
    assert dialog.pages.currentIndex() == 5
    assert dialog._pending_page == 1
    assert not dialog._sweep_moving_down
    assert dialog._page_sweep.x() < 0

    QTest.qWait(210)
    assert dialog.pages.currentIndex() == 1
    assert dialog._page_sweep_out.endValue().x() == host.width()
    QTest.qWait(210)
    assert not dialog._page_sweep.isVisible()
    dialog.hide()
    dialog.deleteLater()


def test_stylesheet_token_replacement_is_single_pass_for_custom_accent():
    tokens = resolve_tokens({"accent": "#F3F8FF"})
    sheet = build_stylesheet(tokens)
    start = sheet.index("QFrame#SettingsPageCurtain")
    curtain = sheet[start : sheet.index("}", start)]

    assert tokens.accent_soft != tokens.accent
    assert f"background: {tokens.accent_soft};" in curtain
    assert f"border-left: 4px solid {tokens.accent};" in curtain


def test_reduced_motion_settings_switches_pages_without_a_curtain(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setenv("SCREEN_TRANSLATOR_MOTION", "reduced")
    dialog = SettingsDialog(AppConfig(tmp_path / "config.json"))
    dialog.resize(900, 540)
    dialog.show()
    qapp.processEvents()

    dialog._select_page(3)
    qapp.processEvents()

    assert dialog.pages.currentIndex() == 3
    assert not dialog._page_sweep.isVisible()
    assert dialog._nav_indicator.geometry().height() > 0
    dialog.hide()
    dialog.deleteLater()


def test_capture_card_keyboard_press_uses_the_same_motion_state(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setenv("SCREEN_TRANSLATOR_MOTION", "reduced")
    window = MainWindow(_Controller(AppConfig(tmp_path / "config.json")))
    window.show()
    window.btn_region.setFocus()
    qapp.processEvents()

    QTest.keyPress(window.btn_region, Qt.Key.Key_Space)
    assert window.btn_region.isDown()
    assert window.btn_region._press == pytest.approx(1.0)
    QTest.keyRelease(window.btn_region, Qt.Key.Key_Space)
    assert window.btn_region._press == pytest.approx(0.0)

    window.hide()
    window.deleteLater()


def test_capture_launch_uses_the_brand_dash_without_outer_corner_marks(
    qapp, tmp_path
):
    window = MainWindow(_Controller(AppConfig(tmp_path / "config.json")))
    card = window.btn_region
    card.resize(420, 120)
    card._launch = 1.0
    image = QImage(card.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)

    card.render(image)

    def is_accent(x: int, y: int) -> bool:
        color = image.pixelColor(x, y)
        return color.blue() > 190 and color.green() > 75 and color.red() < 100

    corner_rois = (
        (range(3, 28), range(3, 28)),
        (range(card.width() - 28, card.width() - 3), range(3, 28)),
        (range(3, 28), range(card.height() - 28, card.height() - 3)),
        (
            range(card.width() - 28, card.width() - 3),
            range(card.height() - 28, card.height() - 3),
        ),
    )
    assert not any(
        is_accent(x, y)
        for xs, ys in corner_rois
        for x in xs
        for y in ys
    )
    assert any(
        is_accent(x, y)
        for x in range(25, 75)
        for y in range(card.height() - 18, card.height() - 4)
    )

    window.finish_capture("region")
    assert card._launch == 0.0
    window.deleteLater()
