"""框选截图遮罩：半透明全屏 + 十字光标 + 拖动矩形。"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import QWidget


class SelectionOverlay(QWidget):
    selection_done = Signal(object)  # QRect（Qt 全局逻辑坐标）
    cancelled = Signal()

    def __init__(
        self,
        geometry: QRect,
        mask_opacity: int = 100,
        border_color: str = "#FF3B30",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.setGeometry(geometry)

        self._origin = None
        self._current = None
        self._accepted = False
        self._mask_opacity = max(20, min(180, mask_opacity))
        self._border = QColor(border_color)
        self._cancelling = False
        self._entrance: QPropertyAnimation | None = None

        self._esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._esc.activated.connect(self.cancel)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.setWindowOpacity(0.0)
        self._entrance = QPropertyAnimation(self, b"windowOpacity", self)
        self._entrance.setDuration(110)
        self._entrance.setStartValue(0.0)
        self._entrance.setEndValue(1.0)
        self._entrance.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._entrance.start()

    def cancel(self) -> None:
        if self._cancelling:
            return
        self._cancelling = True
        self._accepted = False
        self.close()
        self.cancelled.emit()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.position().toPoint()
            self._current = self._origin

    def mouseMoveEvent(self, event) -> None:
        if self._origin is not None:
            self._current = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._origin is not None:
            rect = self._normalized(self._origin, self._current)
            self._accepted = True
            self.close()
            if rect.width() < 4 or rect.height() < 4:
                self.cancelled.emit()
            else:
                self.selection_done.emit(rect)

    def closeEvent(self, event) -> None:
        if not self._accepted and not self._cancelling:
            self.cancelled.emit()
        super().closeEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        mask = QColor("#252826")
        mask.setAlpha(min(150, self._mask_opacity + 20))
        painter.fillRect(self.rect(), mask)

        if self._origin is not None and self._current is not None:
            rect = self._normalized(self._origin, self._current)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            painter.fillRect(rect, Qt.GlobalColor.transparent)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

            pen = QPen(QColor("#2878E8"), 2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)

            font = QFont()
            font.setPixelSize(13)
            painter.setFont(font)
            label = f"{rect.width()} × {rect.height()}"
            metrics = painter.fontMetrics()
            label_rect = QRect(rect.x(), max(4, rect.y() - 28), metrics.horizontalAdvance(label) + 18, 22)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#2878E8"))
            painter.drawRoundedRect(label_rect, 6, 6)
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, label)

    @staticmethod
    def _normalized(a, b) -> QRect:
        return QRect(min(a.x(), b.x()), min(a.y(), b.y()), abs(a.x() - b.x()), abs(a.y() - b.y()))
