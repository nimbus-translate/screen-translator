"""主窗口：按产品视觉稿组织的屏幕翻译工作台。"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from services.ocr.base import list_ocr_engines
from services.translation.base import list_translators
from services.translation.factory import service_display_name
from utils.language_utils import LANGUAGES


class CaptureActionCard(QPushButton):
    """带线性图标和双行说明的捕获入口卡片。"""

    def __init__(self, title: str, detail: str, icon_kind: str, active: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._title = title
        self._detail = detail
        self._icon_kind = icon_kind
        self._active = active
        self._hover = 0.0
        self._hover_animation = QVariantAnimation(self)
        self._hover_animation.setDuration(150)
        self._hover_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hover_animation.valueChanged.connect(self._set_hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(102)

    def _set_hover(self, value) -> None:
        self._hover = float(value)
        self.update()

    def _animate_hover(self, target: float) -> None:
        self._hover_animation.stop()
        self._hover_animation.setStartValue(self._hover)
        self._hover_animation.setEndValue(target)
        self._hover_animation.start()

    def enterEvent(self, event) -> None:
        if self.isEnabled():
            self._animate_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._animate_hover(0.0)
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        active = self._active and self.isEnabled()
        if not self.isEnabled():
            fill = QColor("#FAF9F6")
            border = QColor("#E9E6DF")
        elif active:
            fill = QColor("#F8FBFF")
            border = QColor("#80B4F3")
        else:
            fill = QColor("#FFFEFC")
            border = QColor("#E1DFD9")
            if self._hover:
                fill = QColor("#FAFCFF")
                border = QColor("#9BC3F7")
        painter.setBrush(fill)
        painter.setPen(QPen(border, 1.5))
        painter.drawRoundedRect(rect, 14, 14)

        icon_color = QColor("#B8B8B1") if not self.isEnabled() else (QColor("#2878E8") if active or self._hover > 0.45 else QColor("#343834"))
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
        painter.setPen(QColor("#A8A9A4") if not self.isEnabled() else (QColor("#2878E8") if active or self._hover > 0.45 else QColor("#2A2D2A")))
        text_x = 95
        title_y = max(53, self.height() // 2 - 4)
        painter.drawText(text_x, title_y, self._title)

        detail_font = QFont(title_font)
        detail_font.setPixelSize(12)
        detail_font.setWeight(QFont.Weight.Normal)
        painter.setFont(detail_font)
        painter.setPen(QColor("#A8A9A4") if not self.isEnabled() else QColor("#77766F"))
        painter.drawText(text_x, title_y + 26, self._detail)


class MainWindow(QWidget):
    def __init__(self, controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.setObjectName("Root")
        self.setWindowTitle("屏幕翻译")
        self.setMinimumSize(900, 540)
        self.resize(980, 580)
        self._faded_in = False
        self._build_ui()
        self._load_values()

    def _build_ui(self) -> None:
        self._pages = QStackedLayout(self)
        self._home_page = QWidget()
        self._pages.addWidget(self._home_page)
        layout = QVBoxLayout(self._home_page)
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
        action_row.setContentsMargins(18, 16, 18, 16)
        action_row.setSpacing(16)
        self.btn_region = CaptureActionCard("框选翻译", "开发中", "region", active=False)
        self.btn_fullscreen = CaptureActionCard("全屏翻译", "翻译整个屏幕", "screen")
        self.btn_window = CaptureActionCard("当前窗口", "开发中", "window")
        for card in (self.btn_region, self.btn_fullscreen, self.btn_window):
            action_row.addWidget(card, 1)
        self.btn_region.setEnabled(False)
        self.btn_window.setEnabled(False)
        layout.addWidget(action_panel)

        options_panel = QFrame()
        options_panel.setObjectName("OptionsPanel")
        option_grid = QGridLayout(options_panel)
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
        self.combo_source.setCurrentIndex(self._index_of(self.combo_source, self.controller.config.get("translation.source_language", "auto")))
        self.combo_target.setCurrentIndex(self._index_of(self.combo_target, self.controller.config.get("translation.target_language", "zh")))
        self.combo_ocr.setCurrentIndex(self._index_of(self.combo_ocr, self.controller.config.get("ocr.engine", "paddle")))
        self.combo_service.setCurrentIndex(self._index_of(self.combo_service, self.controller.config.get("translation.service", "mymemory")))
        self.check_keep_original.setChecked(bool(self.controller.config.get("translation.keep_original", False)))
        self._block_signals = False

    @staticmethod
    def _index_of(combo: QComboBox, value: str) -> int:
        return max(0, combo.findData(value))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._faded_in:
            self._faded_in = True
            self.setWindowOpacity(0.0)
            animation = QPropertyAnimation(self, b"windowOpacity", self)
            animation.setDuration(200)
            animation.setStartValue(0.0)
            animation.setEndValue(1.0)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            animation.start()

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def show_settings_page(self, page: QWidget) -> None:
        self._pages.addWidget(page)
        self._pages.setCurrentWidget(page)

    def close_settings_page(self, page: QWidget) -> None:
        self._pages.setCurrentWidget(self._home_page)
        self._pages.removeWidget(page)
        page.deleteLater()

    def set_busy(self, busy: bool) -> None:
        for control in (self.btn_fullscreen, self.btn_refresh):
            control.setEnabled(not busy)

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
