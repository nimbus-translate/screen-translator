"""主窗口：按产品视觉稿组织的屏幕翻译工作台。"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QAbstractAnimation, QPropertyAnimation, QTimer, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from services.ocr.base import list_ocr_engines
from services.translation.base import list_translators
from services.translation.factory import service_display_name
from ui.appearance import current_tokens
from ui.motion import (
    BASE,
    FAST,
    MICRO,
    ENTER_EASING,
    EXIT_EASING,
    MOVE_EASING,
    RELEASE_EASING,
    motion_duration,
)
from ui.settings_transition import SettingsTransitionGuard
from utils.language_utils import LANGUAGES


class NativePageDeck(QWidget):
    """A clipped deck that moves the real pages without a layout fighting them."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("NativePageDeck")
        self._widgets: list[QWidget] = []
        self._current: QWidget | None = None
        self._transition_home: QWidget | None = None
        self._transition_settings: QWidget | None = None
        self._transition_progress = 0.0
        self._transitioning = False

    def addWidget(self, widget: QWidget) -> int:
        index = self.indexOf(widget)
        if index >= 0:
            return index
        widget.setParent(self)
        self._widgets.append(widget)
        widget.setGeometry(self.rect())
        if self._current is None:
            self._current = widget
            widget.show()
        else:
            widget.hide()
        return len(self._widgets) - 1

    def removeWidget(self, widget: QWidget) -> None:
        index = self.indexOf(widget)
        if index < 0:
            return
        widget.hide()
        self._widgets.pop(index)
        if self._current is widget:
            self._current = self._widgets[0] if self._widgets else None
        if self._transition_home is widget or self._transition_settings is widget:
            self._transition_home = None
            self._transition_settings = None
            self._transitioning = False
        if self._current is not None:
            self.setCurrentWidget(self._current)

    def indexOf(self, widget: QWidget) -> int:
        # Qt wrappers may outlive their deleted C++ object. Equality can then
        # dereference the dead object, while identity remains safe.
        for index, candidate in enumerate(self._widgets):
            if candidate is widget:
                return index
        return -1

    def count(self) -> int:
        return len(self._widgets)

    def widget(self, index: int) -> QWidget | None:
        return self._widgets[index] if 0 <= index < len(self._widgets) else None

    def currentWidget(self) -> QWidget | None:
        return self._current

    def isTransitioning(self) -> bool:
        return self._transitioning

    def setCurrentWidget(self, widget: QWidget) -> None:
        if self.indexOf(widget) < 0:
            return
        self._transitioning = False
        self._transition_home = None
        self._transition_settings = None
        self._current = widget
        bounds = self.rect()
        for candidate in self._widgets:
            candidate.setGeometry(bounds)
            candidate.setVisible(candidate is widget)
        widget.raise_()

    def setTransitionProgress(
        self,
        home: QWidget,
        settings: QWidget,
        progress: float,
    ) -> None:
        if self.indexOf(home) < 0 or self.indexOf(settings) < 0:
            return
        self._transition_home = home
        self._transition_settings = settings
        self._transition_progress = max(0.0, min(1.0, float(progress)))
        self._transitioning = True
        self._apply_transition_frame()

    def _apply_transition_frame(self) -> None:
        home = self._transition_home
        settings = self._transition_settings
        if not self._transitioning or home is None or settings is None:
            return
        width = self.width()
        height = self.height()
        seam = round(width * (1.0 - self._transition_progress))
        home.setGeometry(seam - width, 0, width, height)
        settings.setGeometry(seam, 0, width, height)
        home.show()
        settings.show()
        settings.raise_()
        self._current = settings if self._transition_progress >= 0.5 else home

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._transitioning:
            self._apply_transition_frame()
        elif self._current is not None:
            self._current.setGeometry(self.rect())


class CaptureActionCard(QPushButton):
    """带线性图标和双行说明的捕获入口卡片。"""

    def __init__(self, title: str, detail: str, icon_kind: str, active: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._title = title
        self._detail = detail
        self._icon_kind = icon_kind
        self._active = active
        self._hover = 0.0
        self._press = 0.0
        self._launch = 0.0
        self._shake = 0.0
        self._hover_animation = QVariantAnimation(self)
        self._hover_animation.setDuration(BASE)
        self._hover_animation.setEasingCurve(ENTER_EASING)
        self._hover_animation.valueChanged.connect(self._set_hover)
        self._press_animation = QVariantAnimation(self)
        self._press_animation.valueChanged.connect(self._set_press)
        self._launch_animation = QVariantAnimation(self)
        self._launch_animation.valueChanged.connect(self._set_launch)
        self._shake_animation = QVariantAnimation(self)
        self._shake_animation.valueChanged.connect(self._set_shake)
        self.pressed.connect(lambda: self._animate_press(1.0))
        self.released.connect(lambda: self._animate_press(0.0))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(102)

    def _set_hover(self, value) -> None:
        self._hover = float(value)
        self.update()

    def _animate_hover(self, target: float) -> None:
        self._hover_animation.stop()
        duration = motion_duration(BASE)
        if duration == 0:
            self._set_hover(target)
            return
        self._hover_animation.setDuration(duration)
        self._hover_animation.setStartValue(self._hover)
        self._hover_animation.setEndValue(target)
        self._hover_animation.start()

    def _set_press(self, value) -> None:
        self._press = float(value)
        self.update()

    def _animate_press(self, target: float) -> None:
        self._press_animation.stop()
        duration = motion_duration(MICRO if target else FAST)
        if duration == 0:
            self._set_press(target)
            return
        self._press_animation.setDuration(duration)
        self._press_animation.setEasingCurve(
            ENTER_EASING if target else RELEASE_EASING
        )
        self._press_animation.setStartValue(self._press)
        self._press_animation.setEndValue(target)
        self._press_animation.start()

    def _set_launch(self, value) -> None:
        self._launch = float(value)
        self.update()

    def _set_shake(self, value) -> None:
        self._shake = float(value)
        self.update()

    def play_launch(self) -> None:
        self._shake_animation.stop()
        self._launch_animation.stop()
        duration = motion_duration(BASE)
        if duration == 0:
            self._launch = 1.0
            self.update()
            return
        self._launch_animation.setDuration(duration)
        self._launch_animation.setEasingCurve(ENTER_EASING)
        self._launch_animation.setStartValue(self._launch)
        self._launch_animation.setEndValue(1.0)
        self._launch_animation.start()

    def reset_launch(self) -> None:
        self._launch_animation.stop()
        self._launch = 0.0
        self.update()

    def play_failure(self) -> None:
        self.reset_launch()
        self._shake_animation.stop()
        duration = motion_duration(BASE)
        if duration == 0:
            self._shake = 0.0
            self.update()
            return
        self._shake_animation.setDuration(duration)
        self._shake_animation.setEasingCurve(ENTER_EASING)
        self._shake_animation.setKeyValues(
            [(0.0, 0.0), (0.24, -1.0), (0.48, 1.0), (0.72, -0.45), (1.0, 0.0)]
        )
        self._shake_animation.start()

    def set_detail(self, detail: str) -> None:
        self._detail = detail
        self.update()

    @staticmethod
    def _mix(start: QColor, end: QColor, progress: float) -> QColor:
        progress = max(0.0, min(1.0, progress))
        return QColor.fromRgbF(
            start.redF() + (end.redF() - start.redF()) * progress,
            start.greenF() + (end.greenF() - start.greenF()) * progress,
            start.blueF() + (end.blueF() - start.blueF()) * progress,
            start.alphaF() + (end.alphaF() - start.alphaF()) * progress,
        )

    def enterEvent(self, event) -> None:
        if self.isEnabled():
            self._animate_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._animate_hover(0.0)
        self._animate_press(0.0)
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        tokens = current_tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(round(self._shake * 2.2), 0)
        painter.translate(0, round(self._press * 2.0))
        rect = self.rect().adjusted(1, 1, -1, -3)
        visual_enabled = self.isEnabled() or self._launch > 0.0
        active = self._active and visual_enabled
        if not visual_enabled:
            fill = QColor(tokens.surface_alt)
            border = QColor(tokens.border)
        else:
            emphasis = max(self._hover, 0.45 if active else 0.0)
            fill = self._mix(QColor(tokens.surface), QColor(tokens.accent_soft), emphasis)
            border = self._mix(QColor(tokens.border), QColor(tokens.accent_border), emphasis)
        painter.setBrush(fill)
        painter.setPen(QPen(border, 1.5))
        painter.drawRoundedRect(rect, 14, 14)

        emphasis = max(self._hover, 0.45 if active else 0.0)
        icon_color = QColor(tokens.disabled) if not visual_enabled else self._mix(
            QColor(tokens.ink_soft), QColor(tokens.accent), emphasis
        )
        painter.setPen(QPen(icon_color, 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        x, y = 29, max(38, self.height() // 2 - 11)
        if self._icon_kind == "region":
            for px, py, dx, dy in ((x, y + 8, 0, -7), (x, y + 1, 7, 0), (x + 31, y + 8, 0, -7), (x + 31, y + 1, -7, 0),
                                   (x, y + 23, 0, 7), (x, y + 30, 7, 0), (x + 31, y + 23, 0, 7), (x + 31, y + 30, -7, 0)):
                painter.drawLine(px, py, px + dx, py + dy)
            painter.drawLine(x + 35, y + 25, x + 49, y + 25)
            painter.drawLine(x + 42, y + 18, x + 42, y + 32)
        elif self._icon_kind == "screen":
            painter.drawRoundedRect(x, y + 1, 38, 27, 4, 4)
            painter.drawLine(x + 19, y + 28, x + 19, y + 35)
            painter.drawLine(x + 9, y + 35, x + 29, y + 35)
        else:
            painter.drawRoundedRect(x, y, 39, 31, 5, 5)
            painter.drawLine(x, y + 8, x + 39, y + 8)

        title_font = QFont()
        title_font.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"])
        title_font.setPixelSize(17)
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(
            QColor(tokens.disabled)
            if not visual_enabled
            else self._mix(QColor(tokens.ink), QColor(tokens.accent_hover), emphasis)
        )
        text_x = 95
        title_y = max(53, self.height() // 2 - 4)
        painter.drawText(text_x, title_y, self._title)

        detail_font = QFont(title_font)
        detail_font.setPixelSize(12)
        detail_font.setWeight(QFont.Weight.Normal)
        painter.setFont(detail_font)
        painter.setPen(QColor(tokens.disabled) if not visual_enabled else QColor(tokens.muted))
        painter.drawText(text_x, title_y + 26, self._detail)

        if visual_enabled:
            line_width = 12 + round(18 * max(emphasis, self._launch))
            line_x = x + 19 - line_width // 2
            painter.setPen(
                QPen(
                    QColor(tokens.accent), 2.4,
                    Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                )
            )
            painter.drawLine(line_x, rect.bottom() - 10, line_x + line_width, rect.bottom() - 10)

class MainWindow(QWidget):
    def __init__(self, controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.setObjectName("Root")
        self.setWindowTitle("屏幕翻译")
        self.setMinimumSize(900, 540)
        self.resize(980, 580)
        self._faded_in = False
        self._window_capture_available = True
        self._capture_departure_callback: Callable[[], None] | None = None
        self._visibility_action = ""
        self._settings_page_widget: QWidget | None = None
        self._settings_progress = 0.0
        self._settings_transition_target = 0.0
        self._settings_transition_state = "home"
        self._settings_exit_callback: Callable[[], None] | None = None
        self._settings_previous_focus: QWidget | None = None
        self._visibility_animation = QPropertyAnimation(self, b"windowOpacity", self)
        self._visibility_animation.finished.connect(self._finish_visibility_animation)
        self._departure_timer = QTimer(self)
        self._departure_timer.setSingleShot(True)
        self._departure_timer.timeout.connect(self._start_capture_departure)
        self._build_ui()
        self._settings_guard = SettingsTransitionGuard(self)
        self._settings_transition_animation = QVariantAnimation(self)
        self._settings_transition_animation.setStartValue(0.0)
        self._settings_transition_animation.setEndValue(1.0)
        self._settings_transition_animation.valueChanged.connect(self._set_settings_progress)
        self._settings_transition_animation.finished.connect(self._finish_settings_transition)
        self._load_values()
        self.refresh_appearance()

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self._pages = NativePageDeck(self)
        root_layout.addWidget(self._pages)
        self._home_page = QWidget()
        self._home_page.setObjectName("HomePage")
        self._pages.addWidget(self._home_page)
        layout = QVBoxLayout(self._home_page)
        self._home_layout = layout
        layout.setContentsMargins(40, 30, 40, 28)
        layout.setSpacing(22)

        header_frame = QWidget()
        header_frame.setMinimumHeight(94)
        header = QHBoxLayout(header_frame)
        header.setContentsMargins(0, 0, 0, 0)
        title_stack = QVBoxLayout()
        title_stack.setSpacing(7)
        title = QLabel("屏幕翻译")
        title.setObjectName("TitleLabel")
        hint = QLabel("框选、全屏或窗口，一键翻译覆盖")
        hint.setObjectName("HintLabel")
        title_stack.addStretch(1)
        title_stack.addWidget(title)
        title_stack.addWidget(hint)
        title_stack.addStretch(1)
        header.addLayout(title_stack, 1)
        self.btn_settings = QPushButton("设置")
        self.btn_settings.setObjectName("SettingsButton")
        self.btn_settings.setFixedSize(102, 42)
        self.btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        header.addWidget(self.btn_settings, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(header_frame)

        action_panel = QFrame()
        action_panel.setObjectName("ActionPanel")
        action_row = QHBoxLayout(action_panel)
        self._action_layout = action_row
        action_row.setContentsMargins(18, 16, 18, 16)
        action_row.setSpacing(16)
        self.btn_region = CaptureActionCard("框选翻译", "拖拽选择屏幕区域", "region", active=False)
        self.btn_fullscreen = CaptureActionCard("全屏翻译", "翻译整个屏幕", "screen")
        self.btn_window = CaptureActionCard("当前窗口", "翻译当前活动窗口", "window")
        for card in (self.btn_region, self.btn_fullscreen, self.btn_window):
            action_row.addWidget(card, 1)
        layout.addWidget(action_panel)

        options_panel = QFrame()
        options_panel.setObjectName("OptionsPanel")
        option_grid = QGridLayout(options_panel)
        self._option_layout = option_grid
        option_grid.setContentsMargins(22, 18, 22, 20)
        option_grid.setHorizontalSpacing(18)
        option_grid.setVerticalSpacing(9)
        labels = ("源语言", "目标语言", "OCR", "翻译服务")
        for col, label in enumerate(labels):
            caption = QLabel(label)
            caption.setObjectName("SectionLabel")
            option_grid.addWidget(caption, 0, col)
        self.combo_source = QComboBox()
        self.combo_target = QComboBox()
        self.combo_ocr = QComboBox()
        self.combo_service = QComboBox()
        for col, combo in enumerate((self.combo_source, self.combo_target, self.combo_ocr, self.combo_service)):
            combo.setMinimumHeight(48)
            option_grid.addWidget(combo, 1, col)
        option_grid.setColumnStretch(0, 1)
        option_grid.setColumnStretch(1, 1)
        option_grid.setColumnStretch(2, 1)
        option_grid.setColumnStretch(3, 1.55)
        layout.addWidget(options_panel)

        footer_panel = QFrame()
        footer_panel.setObjectName("FooterPanel")
        bottom_row = QHBoxLayout(footer_panel)
        self._footer_layout = bottom_row
        bottom_row.setContentsMargins(22, 14, 18, 14)
        bottom_row.setSpacing(10)
        self.status_label = QLabel("就绪，选择一种方式开始")
        self.status_label.setObjectName("StatusLabel")
        bottom_row.addWidget(self.status_label, 1)
        self.check_keep_original = QCheckBox("保留原文")
        self.btn_toggle = QPushButton("隐藏译文")
        self.btn_toggle.setCheckable(True)
        self.btn_toggle.setObjectName("ToggleButton")
        self.btn_edit = QPushButton("编辑模式")
        self.btn_edit.setCheckable(True)
        self.btn_edit.setObjectName("ToggleButton")
        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.setObjectName("RefreshButton")
        for button in (self.btn_toggle, self.btn_edit, self.btn_refresh):
            button.setMinimumHeight(42)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        bottom_row.addWidget(self.check_keep_original)
        bottom_row.addWidget(self.btn_toggle)
        bottom_row.addWidget(self.btn_edit)
        bottom_row.addWidget(self.btn_refresh)
        layout.addWidget(footer_panel)

        self.btn_region.clicked.connect(self.controller.start_region_capture)
        self.btn_fullscreen.clicked.connect(lambda: self.controller.start_capture("fullscreen"))
        self.btn_window.clicked.connect(lambda: self.controller.start_capture("window"))
        self.btn_settings.clicked.connect(self.controller.open_settings)
        self.btn_toggle.toggled.connect(self._on_toggle_overlay)
        self.btn_edit.toggled.connect(self.controller.set_edit_mode)
        self.btn_refresh.clicked.connect(self.controller.refresh)
        self.check_keep_original.toggled.connect(self._on_keep_original)
        self.combo_source.currentIndexChanged.connect(self._on_combo_changed)
        self.combo_target.currentIndexChanged.connect(self._on_combo_changed)
        self.combo_ocr.currentIndexChanged.connect(self._on_combo_changed)
        self.combo_service.currentIndexChanged.connect(self._on_combo_changed)

    def _load_values(self) -> None:
        self._block_signals = True
        for code, name in LANGUAGES:
            self.combo_source.addItem(name, code)
            self.combo_target.addItem(name, code)
        for engine in list_ocr_engines():
            self.combo_ocr.addItem(engine, engine)
        for service in list_translators():
            self.combo_service.addItem(service_display_name(service), service)
        self._block_signals = False
        self.reload_values()

    def reload_values(self) -> None:
        """Refresh duplicated quick controls after settings are saved."""
        self._block_signals = True
        self.combo_source.setCurrentIndex(self._index_of(self.combo_source, self.controller.config.get("translation.source_language", "auto")))
        self.combo_target.setCurrentIndex(self._index_of(self.combo_target, self.controller.config.get("translation.target_language", "zh")))
        self.combo_ocr.setCurrentIndex(self._index_of(self.combo_ocr, self.controller.config.get("ocr.engine", "paddle")))
        self.combo_service.setCurrentIndex(self._index_of(self.combo_service, self.controller.config.get("translation.service", "mymemory")))
        self.check_keep_original.setChecked(bool(self.controller.config.get("translation.keep_original", False)))
        self._block_signals = False

    def refresh_appearance(self) -> None:
        """Apply density metrics and repaint custom line-art widgets."""
        tokens = current_tokens()
        density = tokens.density
        margins = {
            "spacious": (42, 32, 42, 30),
            "balanced": (40, 30, 40, 28),
            "compact": (32, 22, 32, 22),
        }[density]
        combo_height = {"spacious": 54, "balanced": 48, "compact": 40}[density]
        button_height = {"spacious": 46, "balanced": 42, "compact": 38}[density]
        self._home_layout.setContentsMargins(*margins)
        self._home_layout.setSpacing(tokens.main_spacing)
        self._action_layout.setSpacing(max(12, tokens.main_spacing - 6))
        self._option_layout.setHorizontalSpacing(max(12, tokens.main_spacing - 4))
        self._footer_layout.setSpacing(max(8, tokens.main_spacing - 12))
        self.btn_settings.setFixedHeight(button_height)
        for combo in (self.combo_source, self.combo_target, self.combo_ocr, self.combo_service):
            combo.setMinimumHeight(combo_height)
            combo.setMaximumHeight(combo_height)
        for button in (self.btn_toggle, self.btn_edit, self.btn_refresh):
            button.setMinimumHeight(button_height)
            button.setMaximumHeight(button_height)
        for card in (self.btn_region, self.btn_fullscreen, self.btn_window):
            card.update()
        self.btn_settings.update()
        self.update()

    @staticmethod
    def _index_of(combo: QComboBox, value: str) -> int:
        return max(0, combo.findData(value))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._faded_in:
            self._faded_in = True
            self.setWindowOpacity(0.0)
            duration = motion_duration(BASE, large_surface=True)
            if duration == 0:
                self.setWindowOpacity(1.0)
            else:
                self._visibility_action = "return"
                self._visibility_animation.stop()
                self._visibility_animation.setDuration(duration)
                self._visibility_animation.setStartValue(0.0)
                self._visibility_animation.setEndValue(1.0)
                self._visibility_animation.setEasingCurve(ENTER_EASING)
                self._visibility_animation.start()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_settings_guard"):
            self._settings_guard.setGeometry(self.contentsRect())

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def show_settings_page(self, page: QWidget) -> None:
        # An embedded page must not retain the 900x610 standalone-dialog minimum.
        # The main window deliberately supports 900x540 and never changes size.
        page.setMinimumSize(0, 0)
        if self._pages.indexOf(page) < 0:
            self._pages.addWidget(page)
        if self._settings_progress <= 0.001:
            focused = self.focusWidget()
            self._settings_previous_focus = (
                focused
                if focused is not None
                and (focused is self._home_page or self._home_page.isAncestorOf(focused))
                else self.btn_settings
            )
        self._settings_page_widget = page
        self._settings_exit_callback = None
        self._animate_settings_to(1.0)

    def begin_settings_exit(self, page: QWidget, callback: Callable[[], None]) -> None:
        if page is not self._settings_page_widget or self._pages.indexOf(page) < 0:
            callback()
            return
        if self._settings_transition_state == "exiting":
            return
        self._settings_exit_callback = callback
        self._animate_settings_to(0.0)

    def remove_settings_page(self, page: QWidget) -> None:
        self._pages.setCurrentWidget(self._home_page)
        if self._pages.indexOf(page) >= 0:
            self._pages.removeWidget(page)
        if self._settings_page_widget is page:
            self._settings_page_widget = None
        page.deleteLater()

    def close_settings_page(self, page: QWidget) -> None:
        """Compatibility wrapper for callers that do not need exit coordination."""
        self.begin_settings_exit(page, lambda: self.remove_settings_page(page))

    def _apply_settings_frame(self, progress: float) -> None:
        """Push the two real pages across one exact, gap-free seam."""
        progress = max(0.0, min(1.0, float(progress)))
        page = self._settings_page_widget
        if page is None or self._pages.indexOf(page) < 0:
            return
        self._pages.setTransitionProgress(self._home_page, page, progress)
        if self._settings_guard.isVisible():
            self._settings_guard.raise_()

    def _animate_settings_to(self, target: float) -> None:
        target = 1.0 if target >= 0.5 else 0.0
        live_progress = self._settings_progress
        self._settings_transition_target = target
        self._settings_transition_state = "entering" if target else "exiting"
        popup = QApplication.activePopupWidget()
        if popup is not None:
            popup.close()
        self._settings_guard.setGeometry(self.contentsRect())
        self._apply_settings_frame(live_progress)
        duration = motion_duration(BASE + FAST, large_surface=True)
        distance = abs(target - live_progress)
        if duration == 0 or distance < 0.001:
            self._settings_transition_animation.stop()
            self._set_settings_progress(target)
            self._finish_settings_transition()
            return
        self._settings_guard.show()
        self._settings_guard.raise_()
        self._settings_guard.setFocus(Qt.FocusReason.OtherFocusReason)
        direction = (
            QAbstractAnimation.Direction.Forward
            if target > live_progress
            else QAbstractAnimation.Direction.Backward
        )
        if self._settings_transition_animation.state() == QAbstractAnimation.State.Running:
            # Reverse the live clock; recreating its keyframes causes a visible jump.
            self._settings_transition_animation.setDirection(direction)
        else:
            self._settings_transition_animation.setDuration(max(1, duration))
            self._settings_transition_animation.setEasingCurve(MOVE_EASING)
            curve = self._settings_transition_animation.easingCurve()
            low, high = 0.0, 1.0
            for _ in range(18):
                phase = (low + high) / 2.0
                if curve.valueForProgress(phase) < live_progress:
                    low = phase
                else:
                    high = phase
            live_time = round(duration * (low + high) / 2.0)
            was_blocked = self._settings_transition_animation.blockSignals(True)
            try:
                self._settings_transition_animation.setDirection(direction)
                self._settings_transition_animation.start()
                # start() resets a stopped clock to its directional endpoint.
                # Restore the live clock before any valueChanged frame can run.
                self._settings_transition_animation.setCurrentTime(live_time)
            finally:
                self._settings_transition_animation.blockSignals(was_blocked)
            self._set_settings_progress(live_progress)
        # The moving settings page is raised by the deck. Reassert the guard so
        # there is no one-frame input gap at either edge of the handoff.
        self._settings_guard.raise_()
        self._settings_guard.setFocus(Qt.FocusReason.OtherFocusReason)

    def settle_settings_transition_for_capture(self) -> None:
        """Finish the page handoff before capture visibility takes ownership."""
        if self._settings_transition_state not in {"entering", "exiting"}:
            return
        self._settings_transition_animation.stop()
        self._set_settings_progress(self._settings_transition_target)
        self._finish_settings_transition()

    def _set_settings_progress(self, value) -> None:
        progress = max(0.0, min(1.0, float(value)))
        self._settings_progress = progress
        self._apply_settings_frame(progress)
        page = self._settings_page_widget
        if page is not None and hasattr(page, "set_lifecycle_progress"):
            page.set_lifecycle_progress(progress)
        if 0.0001 < progress < 0.9999:
            self._settings_guard.show()
            self._settings_guard.raise_()

    def _finish_settings_transition(self) -> None:
        target = self._settings_transition_target
        self._set_settings_progress(target)
        self._settings_guard.hide()
        page = self._settings_page_widget
        if target >= 0.5:
            self._settings_transition_state = "active"
            if page is not None and self._pages.indexOf(page) >= 0:
                self._pages.setCurrentWidget(page)
                if hasattr(page, "nav_buttons") and page.nav_buttons:
                    page.nav_buttons[page.pages.currentIndex()].setFocus()
            return
        self._settings_transition_state = "home"
        self._pages.setCurrentWidget(self._home_page)
        previous_focus = self._settings_previous_focus
        self._settings_previous_focus = None
        if (
            previous_focus is not None
            and previous_focus.isEnabled()
            and previous_focus.isVisibleTo(self)
        ):
            previous_focus.setFocus()
        else:
            self.btn_settings.setFocus()
        callback = self._settings_exit_callback
        self._settings_exit_callback = None
        if callback is not None:
            callback()

    def set_busy(self, busy: bool) -> None:
        for control in (
            self.btn_region,
            self.btn_fullscreen,
            self.btn_refresh,
            self.btn_settings,
            self.combo_source,
            self.combo_target,
            self.combo_ocr,
            self.combo_service,
            self.check_keep_original,
            self.btn_toggle,
            self.btn_edit,
        ):
            control.setEnabled(not busy)
        self.btn_window.setEnabled(not busy and self._window_capture_available)

    def set_window_capture_available(self, available: bool, reason: str = "") -> None:
        self._window_capture_available = bool(available)
        self.btn_window.setEnabled(
            self._window_capture_available and not getattr(self.controller, "_busy", False)
        )
        self.btn_window.set_detail(
            "翻译当前活动窗口"
            if self._window_capture_available
            else (reason or "此平台暂不支持窗口捕获")
        )

    def play_capture_departure(self, mode: str, callback: Callable[[], None]) -> None:
        """Confirm the chosen card, then remove the window before any screenshot."""
        self._capture_departure_callback = callback
        card = self._capture_card(mode)
        if card is not None:
            card.play_launch()
        if not self.isVisible():
            self._finish_capture_departure_immediately()
            return
        delay = motion_duration(MICRO, large_surface=True)
        if delay == 0:
            self._start_capture_departure()
        else:
            self._departure_timer.start(delay)

    def restore_after_capture(
        self,
        mode: str,
        *,
        was_minimized: bool = False,
        failed: bool = False,
    ) -> None:
        self._departure_timer.stop()
        self._visibility_animation.stop()
        card = self._capture_card(mode)
        if card is not None:
            card.reset_launch()
        if was_minimized:
            self.setWindowOpacity(1.0)
            self.showMinimized()
            return
        duration = motion_duration(BASE, large_surface=True)
        if duration > 0:
            self.setWindowOpacity(0.0)
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if failed and card is not None:
            card.play_failure()
        if duration == 0:
            self.setWindowOpacity(1.0)
            return
        self._visibility_action = "return"
        self._visibility_animation.setDuration(duration)
        self._visibility_animation.setStartValue(0.0)
        self._visibility_animation.setEndValue(1.0)
        self._visibility_animation.setEasingCurve(ENTER_EASING)
        self._visibility_animation.start()

    def play_capture_failure(self, mode: str) -> None:
        card = self._capture_card(mode)
        if card is not None:
            card.play_failure()

    def finish_capture(self, mode: str) -> None:
        """Clear the hidden launch state before the window is shown again."""
        card = self._capture_card(mode)
        if card is not None:
            card.reset_launch()

    def _capture_card(self, mode: str) -> CaptureActionCard | None:
        return {
            "region": self.btn_region,
            "fullscreen": self.btn_fullscreen,
            "window": self.btn_window,
        }.get(mode)

    def _start_capture_departure(self) -> None:
        duration = motion_duration(FAST, large_surface=True)
        if duration == 0:
            self._finish_capture_departure_immediately()
            return
        self._visibility_animation.stop()
        self._visibility_action = "depart"
        self._visibility_animation.setDuration(duration)
        self._visibility_animation.setStartValue(self.windowOpacity())
        self._visibility_animation.setEndValue(0.0)
        self._visibility_animation.setEasingCurve(EXIT_EASING)
        self._visibility_animation.start()

    def _finish_capture_departure_immediately(self) -> None:
        self.hide()
        self.setWindowOpacity(1.0)
        callback = self._capture_departure_callback
        self._capture_departure_callback = None
        if callback is not None:
            callback()

    def _finish_visibility_animation(self) -> None:
        if self._visibility_action == "depart":
            self._finish_capture_departure_immediately()
        else:
            self.setWindowOpacity(1.0)
        self._visibility_action = ""

    def set_overlay_checked(self, visible: bool) -> None:
        self._block_signals = True
        self.btn_toggle.setChecked(not visible)
        self.btn_toggle.setText("隐藏译文" if visible else "显示译文")
        self._block_signals = False

    def set_edit_mode_checked(self, enabled: bool) -> None:
        self._block_signals = True
        self.btn_edit.setChecked(enabled)
        self._block_signals = False

    def _on_toggle_overlay(self, _checked: bool) -> None:
        if not getattr(self, "_block_signals", False):
            self.controller.toggle_overlay()

    def _on_keep_original(self, checked: bool) -> None:
        if getattr(self, "_block_signals", False):
            return
        self.controller.config.set("translation.keep_original", checked)
        self.controller.config.save()

    def _on_combo_changed(self, _index: int) -> None:
        if getattr(self, "_block_signals", False):
            return
        self.controller.apply_runtime_selection(
            ocr_engine=self.combo_ocr.currentData(), service=self.combo_service.currentData(),
            source=self.combo_source.currentData(), target=self.combo_target.currentData(),
        )

    def closeEvent(self, event) -> None:
        if self.controller.config.get("general.minimize_to_tray", True):
            event.ignore()
            self.hide()
        else:
            super().closeEvent(event)
