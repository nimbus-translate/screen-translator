"""Animated virtual-desktop selection surface for region capture."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, Signal, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import QWidget

from ui.motion import (
    BASE,
    FAST,
    SCAN_CYCLE,
    ENTER_EASING,
    EXIT_EASING,
    continuous_motion_enabled,
    motion_duration,
)


_ACCENT = "#2878E8"
_SURFACE = "#FFFEFC"
_INK = "#292D2A"
_HAIRLINE = "#DEDCD5"


class SelectionOverlay(QWidget):
    # Global Qt logical coordinates, right/bottom exclusive.
    selection_done = Signal(object)
    cancelled = Signal()

    def __init__(
        self,
        geometry: QRect,
        mask_opacity: int = 100,
        border_color: str = "#2878E8",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.setGeometry(geometry)

        # Mouse events are local to this top-level surface. Preserve the virtual
        # desktop origin so left/upper monitors keep their negative coordinates.
        self._virtual_origin = QPoint(geometry.x(), geometry.y())
        self._origin: QPoint | None = None
        self._current: QPoint | None = None
        self._cursor = self.rect().center()
        self._mask_opacity = max(20, min(180, int(mask_opacity)))
        self._border = QColor(_ACCENT)
        self.set_accent(border_color)

        self._finishing = False
        self._signal_emitted = False
        self._pending_bbox: tuple[int, int, int, int] | None = None
        self._finish_cancelled = False
        self._finish_progress = 0.0
        self._scan_progress = 0.0

        self._entrance = QVariantAnimation(self)
        self._entrance.valueChanged.connect(self._set_entrance_progress)
        self._entrance.setEasingCurve(ENTER_EASING)

        self._finish_animation = QVariantAnimation(self)
        self._finish_animation.valueChanged.connect(self._set_finish_progress)
        self._finish_animation.finished.connect(self._complete_finish)

        self._scan_animation = QVariantAnimation(self)
        self._scan_animation.setStartValue(0.0)
        self._scan_animation.setEndValue(1.0)
        self._scan_animation.setDuration(SCAN_CYCLE)
        self._scan_animation.setLoopCount(-1)
        self._scan_animation.valueChanged.connect(self._set_scan_progress)

        self._esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._esc.activated.connect(self.cancel)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._entrance.stop()
        duration = motion_duration(FAST, large_surface=True)
        if duration == 0:
            self.setWindowOpacity(1.0)
        else:
            self.setWindowOpacity(0.0)
            self._entrance.setDuration(duration)
            self._entrance.setStartValue(0.0)
            self._entrance.setEndValue(1.0)
            self._entrance.start()

    def dismiss(self) -> None:
        """Remove the surface without emitting a user-cancel signal."""
        self._signal_emitted = True
        self._finishing = True
        self._entrance.stop()
        self._finish_animation.stop()
        self._scan_animation.stop()
        self.hide()

    def set_accent(self, color: str) -> None:
        """Update the capture accent without changing the neutral overlay."""
        accent = QColor(str(color).strip())
        if not accent.isValid() or accent.alpha() != 255:
            accent = QColor(_ACCENT)
        self._border = accent
        self.update()

    def cancel(self) -> None:
        self._begin_finish(cancelled=True)

    def mousePressEvent(self, event) -> None:
        if self._finishing:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.position().toPoint()
            self._current = self._origin
            self._cursor = self._origin
            self._scan_progress = 0.0
            if continuous_motion_enabled():
                self._scan_animation.stop()
                self._scan_animation.start()
            self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._finishing:
            return
        self._cursor = event.position().toPoint()
        if self._origin is not None:
            self._current = self._cursor
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._finishing:
            return
        if event.button() != Qt.MouseButton.LeftButton or self._origin is None:
            return
        # Release can arrive without a final move event; it is the authoritative edge.
        self._current = event.position().toPoint()
        self._cursor = self._current
        rect = self._normalized(self._origin, self._current)
        if rect.width() < 4 or rect.height() < 4:
            self._begin_finish(cancelled=True)
            return
        left = self._virtual_origin.x() + rect.x()
        top = self._virtual_origin.y() + rect.y()
        self._begin_finish(
            cancelled=False,
            bbox=(left, top, left + rect.width(), top + rect.height()),
        )

    def closeEvent(self, event) -> None:
        self._entrance.stop()
        self._finish_animation.stop()
        self._scan_animation.stop()
        if not self._signal_emitted and not self._finishing:
            self._signal_emitted = True
            self.cancelled.emit()
        super().closeEvent(event)

    def _begin_finish(
        self,
        *,
        cancelled: bool,
        bbox: tuple[int, int, int, int] | None = None,
    ) -> None:
        if self._finishing or self._signal_emitted:
            return
        self._finishing = True
        self._finish_cancelled = cancelled
        self._pending_bbox = bbox
        self._scan_animation.stop()
        self._finish_animation.stop()
        duration = motion_duration(FAST, large_surface=True)
        if duration == 0:
            self._finish_progress = 1.0
            self._complete_finish()
            return
        self._finish_animation.setDuration(duration)
        self._finish_animation.setStartValue(0.0)
        self._finish_animation.setEndValue(1.0)
        self._finish_animation.setEasingCurve(
            EXIT_EASING if cancelled else ENTER_EASING
        )
        self._finish_animation.start()

    def _complete_finish(self) -> None:
        if self._signal_emitted:
            return
        self._signal_emitted = True
        self.hide()
        if self._finish_cancelled or self._pending_bbox is None:
            self.cancelled.emit()
        else:
            self.selection_done.emit(self._pending_bbox)

    def _set_entrance_progress(self, value) -> None:
        self.setWindowOpacity(float(value))

    def _set_finish_progress(self, value) -> None:
        self._finish_progress = float(value)
        # Keep the selection visible long enough to read the confirmation pulse,
        # then clear the entire top-level surface before capture begins.
        fade = max(0.0, (self._finish_progress - 0.34) / 0.66)
        self.setWindowOpacity(1.0 - fade)
        self.update()

    def _set_scan_progress(self, value) -> None:
        self._scan_progress = float(value)
        if self.isVisible():
            self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        mask = QColor("#252826")
        mask.setAlpha(self._mask_opacity)
        painter.fillRect(self.rect(), mask)

        if self._origin is None or self._current is None:
            self._paint_idle_guides(painter)
            return

        rect = self._normalized(self._origin, self._current)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.fillRect(rect, Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        pulse = 1.0 - abs(1.0 - self._finish_progress * 2.0)
        border = QColor(self._border)
        border.setAlpha(220)
        painter.setPen(
            QPen(
                border,
                1.6 + (0.8 * pulse if self._finishing and not self._finish_cancelled else 0.0),
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

        # One travelling accent dash carries the icon motif without turning the
        # selection into a scanner or targeting reticle.
        if not self._finishing and rect.width() > 42:
            dash_length = min(34, max(18, rect.width() // 7))
            travel = max(0, rect.width() - dash_length - 16)
            dash_x = rect.left() + 8 + round(travel * self._scan_progress)
            painter.setPen(
                QPen(self._border, 3.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            )
            painter.drawLine(dash_x, rect.top(), dash_x + dash_length, rect.top())

        self._paint_size_badge(painter, rect)

    def _paint_idle_guides(self, painter: QPainter) -> None:
        text = "拖拽选择要翻译的区域    Esc 取消"
        font = QFont()
        font.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"])
        font.setPixelSize(13)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text) + 42
        badge = QRect(max(12, (self.width() - width) // 2), 24, width, 38)
        painter.setPen(QPen(QColor(_HAIRLINE), 1))
        painter.setBrush(QColor(_SURFACE))
        painter.drawRoundedRect(badge, 12, 12)
        painter.setPen(QPen(self._border, 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(badge.left() + 13, badge.center().y(), badge.left() + 23, badge.center().y())
        painter.setPen(QColor(_INK))
        painter.drawText(badge.adjusted(31, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter, text)

    def _paint_size_badge(self, painter: QPainter, rect: QRect) -> None:
        font = QFont()
        font.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"])
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        label = f"{rect.width()} × {rect.height()}"
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(label) + 38
        height = 28
        x = min(max(4, rect.left()), max(4, self.width() - width - 4))
        preferred_y = rect.top() - height - 6
        if preferred_y < 4:
            preferred_y = rect.bottom() + 6
        y = min(max(4, preferred_y), max(4, self.height() - height - 4))
        badge = QRect(x, y, width, height)
        painter.setPen(QPen(QColor(_HAIRLINE), 1))
        painter.setBrush(QColor(_SURFACE))
        painter.drawRoundedRect(badge, 9, 9)
        painter.setPen(QPen(self._border, 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(badge.left() + 10, badge.center().y(), badge.left() + 20, badge.center().y())
        painter.setPen(self._border)
        painter.drawText(
            badge.adjusted(27, 0, -7, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            label,
        )

    @staticmethod
    def _normalized(a: QPoint, b: QPoint) -> QRect:
        return QRect(
            min(a.x(), b.x()),
            min(a.y(), b.y()),
            abs(a.x() - b.x()),
            abs(a.y() - b.y()),
        )
