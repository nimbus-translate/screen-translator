"""Shared motion tokens and accessibility-aware animation policy."""

from __future__ import annotations

import os
from enum import Enum

from PySide6.QtCore import QEasingCurve, Qt
from PySide6.QtWidgets import QApplication


MICRO = 80
FAST = 120
BASE = 180
SLOW = 240

CAPTURE_SETTLE = 90
SELECTION_SETTLE = 50
STATUS_HOLD = 900
SCAN_CYCLE = 960
WAVE_FRAME = 40

ENTER_EASING = QEasingCurve.Type.OutCubic
EXIT_EASING = QEasingCurve.Type.InCubic
MOVE_EASING = QEasingCurve.Type.InOutCubic
RELEASE_EASING = QEasingCurve.Type.OutBack


class MotionMode(str, Enum):
    FULL = "full"
    REDUCED = "reduced"
    ECO = "eco"


_profile = "flow"
_reduce_motion = False


def configure_motion(profile: str = "flow", reduce_motion: bool = False) -> None:
    """Update the user motion preference without rebuilding animation objects."""
    global _profile, _reduce_motion
    _profile = profile if profile in {"flow", "calm", "minimal"} else "flow"
    _reduce_motion = bool(reduce_motion)


def motion_profile() -> str:
    return _profile


def motion_mode() -> MotionMode:
    override = os.environ.get("SCREEN_TRANSLATOR_MOTION", "").strip().lower()
    if override in {mode.value for mode in MotionMode}:
        return MotionMode(override)

    if _reduce_motion:
        return MotionMode.REDUCED

    app = QApplication.instance()
    if app is not None and not app.isEffectEnabled(Qt.UIEffect.UI_General):
        return MotionMode.REDUCED

    session = os.environ.get("SESSIONNAME", "").upper()
    if session.startswith("RDP-") or os.environ.get("SCREEN_TRANSLATOR_ECO") == "1":
        return MotionMode.ECO
    return MotionMode.FULL


def motion_duration(milliseconds: int, *, large_surface: bool = False) -> int:
    mode = motion_mode()
    if mode is MotionMode.REDUCED:
        return 0
    if mode is MotionMode.ECO and large_surface:
        return 0
    factor = {"flow": 1.0, "calm": 1.28, "minimal": 0.72}.get(_profile, 1.0)
    return max(0, int(round(milliseconds * factor)))


def continuous_motion_enabled() -> bool:
    return motion_mode() is not MotionMode.REDUCED and _profile != "minimal"


def motion_frame_interval() -> int:
    if motion_mode() is MotionMode.ECO:
        return 100
    return {"flow": WAVE_FRAME, "calm": 58, "minimal": 100}.get(_profile, WAVE_FRAME)
