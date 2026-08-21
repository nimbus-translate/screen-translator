"""Short, non-interactive confirmation outline for current-window capture."""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt, Signal, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ui.motion import SLOW, ENTER_EASING, motion_duration


class WindowCaptureHighlight(QWidget):
    finished = Signal()

    def __init__(
        self,
        geometry: QRect,
        title: str = "",
        parent=None,
        *,
        accent_color: str = "#2878E8",
    ) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(geometry.adjusted(-9, -9, 9, 9))
        self._title = title.strip()
        self._accent = QColor("#2878E8")
        self.set_accent(accent_color)
        self._progress = 0.0
        self._emitted = False
        self._animation = QVariantAnimation(self)
        self._animation.setEasingCurve(ENTER_EASING)
        self._animation.valueChanged.connect(self._set_progress)
        self._animation.finished.connect(self._complete)

    def show_and_confirm(self) -> None:
        self._emitted = False
        self._animation.stop()
        duration = motion_duration(SLOW, large_surface=True)
        if duration == 0:
            self.hide()
            self._complete()
            return
        self._progress = 0.0
        self.show()
        self.raise_()
        self._animation.setDuration(duration)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.start()

    def dismiss(self) -> None:
        self._animation.stop()
        self._emitted = True
        self.hide()

    def set_accent(self, color: str) -> None:
        """Refresh the confirmation accent while preserving neutral surfaces."""
        accent = QColor(str(color).strip())
        if not accent.isValid() or accent.alpha() != 255:
            accent = QColor("#2878E8")
        self._accent = accent
        self.update()

    def _set_progress(self, value) -> None:
        self._progress = float(value)
        self.update()

    def _complete(self) -> None:
        if self._emitted:
            return
        self._emitted = True
        self.hide()
        self.finished.emit()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        frame = self.rect().adjusted(9, 9, -10, -10)
        draw_progress = min(1.0, self._progress / 0.72)
        fade = max(0.0, (self._progress - 0.78) / 0.22)
        painter.setOpacity(1.0 - fade)

        accent = QColor(self._accent)
        outline = QColor(accent)
        outline.setAlpha(125)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(outline, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawRoundedRect(frame, 12, 12)

        # Match the app icon: one concise accent dash, not targeting corners.
        painter.setPen(QPen(accent, 3.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        length = round(min(42, max(18, frame.width() * 0.08)) * draw_progress)
        painter.drawLine(frame.left() + 16, frame.top(), frame.left() + 16 + length, frame.top())

        if draw_progress > 0.35:
            label = "已锁定窗口"
            if self._title:
                label += f" · {self._title[:28]}"
            font = QFont()
            font.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"])
            font.setPixelSize(12)
            painter.setFont(font)
            metrics = painter.fontMetrics()
            available = max(0, self.width() - 24)
            width = min(available, max(72, metrics.horizontalAdvance(label) + 38))
            if width < 54:
                return
            badge = QRect(12, 12, width, 32)
            badge_border = QColor(accent)
            badge_border.setAlpha(92)
            painter.setPen(QPen(badge_border, 1))
            painter.setBrush(QColor(255, 254, 252, 242))
            painter.drawRoundedRect(badge, 10, 10)
            painter.setPen(QPen(accent, 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(badge.left() + 11, badge.center().y(), badge.left() + 21, badge.center().y())
            painter.setPen(QColor("#292D2A"))
            visible_label = metrics.elidedText(
                label,
                Qt.TextElideMode.ElideRight,
                max(1, badge.width() - 39),
            )
            painter.drawText(
                badge.adjusted(29, 0, -8, 0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                visible_label,
            )
