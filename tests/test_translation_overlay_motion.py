"""Focused regressions for the translation overlay motion state machine."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QAbstractAnimation, QRectF
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.config import AppConfig
from ui.motion import FAST, SLOW
from ui.translation_overlay import Block, TranslationOverlayWindow


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _overlay(tmp_path) -> TranslationOverlayWindow:
    overlay = TranslationOverlayWindow(AppConfig(tmp_path / "config.json"))
    overlay.resize(360, 220)
    return overlay


def _blocks() -> list[Block]:
    # Deliberately not in reading order: the animation must sort visually
    # without mutating the caller's list.
    return [
        Block(QRectF(24, 142, 150, 30), "bottom", QColor("#FFFFFF")),
        Block(QRectF(184, 24, 140, 30), "top right", QColor("#FFFFFF")),
        Block(QRectF(24, 24, 140, 30), "top left", QColor("#FFFFFF")),
    ]


def test_reveal_is_staggered_in_visual_reading_order(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("SCREEN_TRANSLATOR_MOTION", "full")
    overlay = _overlay(tmp_path)
    blocks = _blocks()
    overlay.set_blocks(blocks)

    assert overlay._blocks == blocks
    assert overlay._reveal_ranks == {2: 0, 1: 1, 0: 2}
    overlay._set_reveal_progress(0.45)
    progress = [overlay._block_reveal_progress(index) for index in range(3)]
    assert progress[2] > progress[1] > progress[0]

    overlay._set_reveal_progress(1.0)
    assert [overlay._block_reveal_progress(index) for index in range(3)] == [1.0] * 3
    overlay.deleteLater()


def test_full_motion_reuses_objects_and_does_not_replay_old_content(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setenv("SCREEN_TRANSLATOR_MOTION", "full")
    overlay = _overlay(tmp_path)
    overlay.set_blocks(_blocks())
    fade = overlay._fade_animation
    hide = overlay._hide_animation
    reveal = overlay._reveal_animation

    overlay.show_fade()
    assert fade.state() == QAbstractAnimation.State.Running
    assert reveal.state() == QAbstractAnimation.State.Running
    QTest.qWait(SLOW + 80)
    assert overlay.isVisible()
    assert overlay.windowOpacity() == pytest.approx(1.0, abs=0.03)
    assert overlay._reveal_progress == pytest.approx(1.0)

    overlay.hide_fade()
    assert hide.state() == QAbstractAnimation.State.Running
    QTest.qWait(FAST + 50)
    assert not overlay.isVisible()

    # Showing the same translated result again only fades the window. The
    # block stagger is reserved for genuinely new content.
    overlay.show_fade()
    assert reveal.state() == QAbstractAnimation.State.Stopped
    QTest.qWait(200)
    assert overlay._reveal_progress == pytest.approx(1.0)

    for _ in range(25):
        overlay.hide_fade()
        overlay.show_fade()
    assert overlay._fade_animation is fade
    assert overlay._hide_animation is hide
    assert overlay._reveal_animation is reveal
    overlay.hide()
    overlay.deleteLater()


@pytest.mark.parametrize("mode", ["reduced", "eco"])
def test_large_surface_motion_is_immediate_in_reduced_and_eco(
    qapp, tmp_path, monkeypatch, mode
):
    monkeypatch.setenv("SCREEN_TRANSLATOR_MOTION", mode)
    overlay = _overlay(tmp_path)
    overlay.set_blocks(_blocks())

    overlay.show_fade()
    qapp.processEvents()
    assert overlay.isVisible()
    assert overlay.windowOpacity() == pytest.approx(1.0)
    assert overlay._reveal_progress == pytest.approx(1.0)
    assert overlay._fade_animation.state() == QAbstractAnimation.State.Stopped
    assert overlay._reveal_animation.state() == QAbstractAnimation.State.Stopped

    overlay.hide_fade()
    assert not overlay.isVisible()
    assert overlay.windowOpacity() == pytest.approx(0.0)
    assert overlay._hide_animation.state() == QAbstractAnimation.State.Stopped
    overlay.deleteLater()


def test_interrupted_reveal_resumes_from_live_progress(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("SCREEN_TRANSLATOR_MOTION", "full")
    overlay = _overlay(tmp_path)
    overlay.set_blocks(_blocks())
    overlay.show_fade()
    QTest.qWait(70)
    before_hide = overlay._reveal_progress
    assert 0.0 < before_hide < 1.0

    overlay.hide_fade()
    assert overlay._reveal_animation.state() == QAbstractAnimation.State.Stopped
    frozen = overlay._reveal_progress
    QTest.qWait(30)
    assert overlay._reveal_progress == pytest.approx(frozen)

    overlay.show_fade()
    assert overlay._reveal_progress >= frozen - 0.01
    QTest.qWait(SLOW + 80)
    assert overlay.isVisible()
    assert overlay._reveal_progress == pytest.approx(1.0)
    overlay.hide()
    overlay.deleteLater()


def test_edit_mode_finishes_reveal_without_allocating_animation(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setenv("SCREEN_TRANSLATOR_MOTION", "full")
    overlay = _overlay(tmp_path)
    overlay.set_blocks(_blocks())
    reveal = overlay._reveal_animation
    overlay.show_fade()
    QTest.qWait(40)
    assert reveal.state() == QAbstractAnimation.State.Running

    overlay.set_edit_mode(True)
    assert overlay._reveal_animation is reveal
    assert reveal.state() == QAbstractAnimation.State.Stopped
    assert overlay._reveal_progress == pytest.approx(1.0)
    overlay.hide()
    overlay.deleteLater()
