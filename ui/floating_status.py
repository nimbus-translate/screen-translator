"""翻译中浮动提示气泡：置顶、鼠标穿透、粉色加载动画。"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPoint, QParallelAnimationGroup, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QWidget


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
        self._timer.setInterval(110)
        self._timer.timeout.connect(self._tick)
        self._fade_in: QPropertyAnimation | None = None
        self._fade_out: QPropertyAnimation | None = None
        self._enter_group: QParallelAnimationGroup | None = None
        self.resize(248, 58)
        self._position_center()

    def _position_center(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.center().x() - self.width() // 2, area.y() + int(area.height() * 0.18))

    def set_text(self, text: str) -> None:
        self._text = text
        self.update()

    def show_fade(self, text: str | None = None) -> None:
        if text:
            self._text = text
        self._position_center()
        self.show()
        self.raise_()
        self.setWindowOpacity(0.0)
        self._fade_out = None
        self._enter_group = QParallelAnimationGroup(self)
        self._fade_in = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_in.setDuration(170)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)
        lift = QPropertyAnimation(self, b"pos", self)
        end_pos = self.pos()
        lift.setDuration(190)
        lift.setStartValue(end_pos + QPoint(0, 8))
        lift.setEndValue(end_pos)
        lift.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._enter_group.addAnimation(self._fade_in)
        self._enter_group.addAnimation(lift)
        self._enter_group.start()
        if not self._timer.isActive():
            self._timer.start()

    def hide_fade(self, delay_ms: int = 0) -> None:
        def _run() -> None:
            self._fade_in = None
            self._fade_out = QPropertyAnimation(self, b"windowOpacity", self)
            self._fade_out.setDuration(180)
            self._fade_out.setStartValue(self.windowOpacity())
            self._fade_out.setEndValue(0.0)
            self._fade_out.setEasingCurve(QEasingCurve.Type.InCubic)
            self._fade_out.finished.connect(self._really_hide)
            self._fade_out.start()

        if delay_ms > 0:
            QTimer.singleShot(delay_ms, _run)
        else:
            _run()

    def _really_hide(self) -> None:
        self._timer.stop()
        self.hide()

    def _tick(self) -> None:
        self._phase += 1
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(6, 6, -6, -6)
        path = QPainterPath()
        path.addRoundedRect(rect, 14, 14)
        painter.fillPath(path, QColor("#FFFEFC"))
        painter.setPen(QColor("#DEDCD5"))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        # 三个跳动圆点
        dot_y = rect.center().y()
        for index in range(3):
            offset = (self._phase + index * 2) % 6
            radius = 3.0 + (1.4 if offset < 3 else 0.0)
            x = rect.left() + 24 + index * 18
            color = QColor(40, 120, 232, 230 - index * 35)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(
                int(x - radius), int(dot_y - radius), int(radius * 2), int(radius * 2)
            )

        font = QFont()
        font.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei", "SimSun"])
        font.setPointSize(10)
        painter.setFont(font)
        painter.setPen(QColor("#30322F"))
        painter.drawText(
            rect.adjusted(70, 0, -10, 0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._text,
        )
