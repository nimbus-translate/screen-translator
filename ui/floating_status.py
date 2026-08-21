"""翻译中浮动提示气泡：置顶、鼠标穿透、蓝色波点动画。"""

from __future__ import annotations

import math

from PySide6.QtCore import (
    QAbstractAnimation,
    QPoint,
    QParallelAnimationGroup,
    QPropertyAnimation,
    Qt,
    QTimer,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QWidget

from ui.appearance import current_tokens
from ui.motion import (
    BASE,
    ENTER_EASING,
    EXIT_EASING,
    continuous_motion_enabled,
    motion_duration,
    motion_frame_interval,
)


class FloatingStatus(QWidget):
    """屏幕中央偏上的"正在翻译…"提示，带三点跳动动画。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._text = "正在翻译…"
        self._phase = 0
        self._timer = QTimer(self)
        self._timer.setInterval(motion_frame_interval())
        self._timer.timeout.connect(self._tick)
        self._hide_delay_timer = QTimer(self)
        self._hide_delay_timer.setSingleShot(True)
        self._hide_delay_timer.timeout.connect(self._start_fade_out)

        self._enter_group = QParallelAnimationGroup(self)
        self._fade_in = QPropertyAnimation(self, b"windowOpacity", self._enter_group)
        self._fade_in.setDuration(BASE)
        self._fade_in.setEasingCurve(ENTER_EASING)
        self._lift = QPropertyAnimation(self, b"pos", self._enter_group)
        self._lift.setDuration(BASE)
        self._lift.setEasingCurve(ENTER_EASING)
        self._enter_group.addAnimation(self._fade_in)
        self._enter_group.addAnimation(self._lift)

        self._fade_out = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_out.setDuration(BASE)
        self._fade_out.setEndValue(0.0)
        self._fade_out.setEasingCurve(EXIT_EASING)
        self._fade_out.finished.connect(self._really_hide)
        self.resize(248, 58)
        self._position_center()

    def refresh_appearance(self) -> None:
        self._timer.setInterval(motion_frame_interval())
        if not continuous_motion_enabled():
            self._stop_dot_timer()
        elif self.isVisible():
            self._sync_dot_timer()
        self.update()

    def _position_center(self, anchor: QPoint | None = None) -> None:
        screen = QApplication.screenAt(anchor) if anchor is not None else None
        screen = screen or QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.center().x() - self.width() // 2, area.y() + int(area.height() * 0.18))

    def set_text(self, text: str) -> None:
        self._text = text
        self.update()

    def show_fade(self, text: str | None = None, anchor: QPoint | None = None) -> None:
        if text:
            self._text = text
        was_fading_out = self._fade_out.state() == QAbstractAnimation.State.Running
        self._hide_delay_timer.stop()
        self._fade_out.stop()
        self._position_center(anchor)
        end_pos = self.pos()

        # A worker can report status many times per second. Stable or currently
        # entering bubbles only change their text; replaying the entrance would flicker.
        if self.isVisible() and not was_fading_out:
            self.raise_()
            self._sync_dot_timer()
            self.update()
            return

        duration = motion_duration(BASE, large_surface=False)
        if duration == 0:
            self._enter_group.stop()
            self.setWindowOpacity(1.0)
            self.move(end_pos)
            self.show()
            self.raise_()
            self._stop_dot_timer()
            self.update()
            return

        self._enter_group.stop()
        start_opacity = self.windowOpacity() if self.isVisible() else 0.0
        start_pos = end_pos if self.isVisible() else end_pos + QPoint(0, 8)
        self.setWindowOpacity(start_opacity)
        self.move(start_pos)
        self._fade_in.setStartValue(start_opacity)
        self._fade_in.setEndValue(1.0)
        self._fade_in.setDuration(duration)
        self._lift.setStartValue(start_pos)
        self._lift.setEndValue(end_pos)
        self._lift.setDuration(duration)
        self.show()
        self.raise_()
        self._enter_group.start()
        self._sync_dot_timer()

    def _sync_dot_timer(self) -> None:
        if continuous_motion_enabled():
            if not self._timer.isActive():
                self._timer.start()
            return
        self._stop_dot_timer()

    def _stop_dot_timer(self) -> None:
        self._timer.stop()
        self._phase = 0

    def hide_fade(self, delay_ms: int = 0) -> None:
        self._hide_delay_timer.stop()
        if delay_ms > 0:
            self._hide_delay_timer.start(delay_ms)
        else:
            self._start_fade_out()

    def _start_fade_out(self) -> None:
        if not self.isVisible():
            return
        self._enter_group.stop()
        self._fade_out.stop()
        duration = motion_duration(BASE)
        if duration == 0:
            self._really_hide()
            return
        self._fade_out.setDuration(duration)
        self._fade_out.setStartValue(self.windowOpacity())
        self._fade_out.start()

    def hide_immediate(self) -> None:
        """Remove every top-level status pixel before taking a screenshot."""
        self._hide_delay_timer.stop()
        self._enter_group.stop()
        self._fade_out.stop()
        self._stop_dot_timer()
        self.setWindowOpacity(0.0)
        self.hide()

    def _really_hide(self) -> None:
        self._stop_dot_timer()
        self.hide()

    def _tick(self) -> None:
        self._phase += 1
        self.update()

    def paintEvent(self, event) -> None:
        tokens = current_tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(6, 6, -6, -6)
        path = QPainterPath()
        path.addRoundedRect(rect, 14, 14)
        painter.fillPath(path, QColor(tokens.surface))
        painter.setPen(QColor(tokens.border))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        # 三颗相位错开的波点，运动连续而非生硬闪烁。
        dot_y = rect.center().y()
        for index in range(3):
            wave = (math.sin((self._phase - index * 4) * math.pi / 9.0) + 1.0) / 2.0
            radius = 2.7 + 0.9 * wave
            lift = round(3.0 * wave)
            x = rect.left() + 24 + index * 18
            color = QColor(tokens.accent)
            color.setAlpha(230 - index * 35)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(
                int(x - radius), int(dot_y - lift - radius), int(radius * 2), int(radius * 2)
            )

        font = QFont()
        font.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei", "SimSun"])
        font.setPointSize(10)
        painter.setFont(font)
        painter.setPen(QColor(tokens.ink))
        painter.drawText(
            rect.adjusted(70, 0, -10, 0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._text,
        )
