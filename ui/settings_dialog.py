"""设置对话框：通用 / OCR / 翻译 / 覆盖层 / 快捷键。"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QScrollArea,
    QTabWidget,
    QFrame,
    QStackedWidget,
    QVBoxLayout,
    QCheckBox,
    QWidget,
)

from app.config import AppConfig
from app.hotkeys import HotkeyError, normalize_hotkey
from app.logger import get_logger
from services.ocr.base import list_ocr_engines
from services.translation.base import list_translators
from services.translation.factory import service_display_name
from utils.language_utils import LANGUAGES

log = get_logger("settings")

_HOTKEY_ACTIONS = [
    ("capture_region", "框选翻译"),
    ("capture_fullscreen", "全屏翻译"),
    ("capture_window", "当前窗口翻译"),
    ("toggle_overlay", "隐藏/显示译文"),
    ("refresh", "重新识别翻译"),
]


class SettingsGlyph(QWidget):
    """Small line-art mark shared with the application icon language."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(58, 58)
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
        self._icon = QPixmap(str(root / "assets" / "app_launch_v4.png"))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._icon.isNull():
            painter.drawPixmap(self.rect(), self._icon)
            return
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.setBrush(QColor("#FFFEFC"))
        painter.setPen(QPen(QColor("#E4E1DA"), 1.2))
        painter.drawRoundedRect(rect, 16, 16)
        painter.setPen(QPen(QColor("#292D2A"), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(17, 25, 17, 18)
        painter.drawLine(17, 18, 25, 18)
        painter.drawLine(40, 32, 40, 40)
        painter.drawLine(40, 40, 32, 40)
        painter.drawRoundedRect(22, 23, 17, 13, 3, 3)
        painter.drawLine(28, 36, 28, 39)
        painter.drawLine(28, 39, 32, 36)
        painter.drawLine(27, 28, 34, 28)
        painter.drawLine(27, 32, 32, 32)
        painter.setPen(QPen(QColor("#2878E8"), 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(24, 44, 34, 44)


class SettingsDialog(QWidget):
    accepted = Signal()
    finished = Signal(int)
    def __init__(self, config: AppConfig, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("设置")
        self.setMinimumSize(900, 610)
        self.resize(1020, 680)
        self._page_sweep_in: QPropertyAnimation | None = None
        self._page_sweep_out: QPropertyAnimation | None = None
        self._active_page = 0
        self._embedded = False
        self._build_ui()
        self._load()

    def show_inside(self, parent: QWidget) -> None:
        self._embedded = True
        self.setWindowFlags(Qt.WindowType.Widget)
        self.setMinimumSize(0, 0)
        parent.installEventFilter(self)
        self.setGeometry(parent.contentsRect())
        self.show()
        self.raise_()

    def eventFilter(self, watched, event) -> bool:
        if self._embedded and watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self.setGeometry(watched.contentsRect())
        return super().eventFilter(watched, event)

    def accept(self) -> None:
        self.accepted.emit()
        self.finished.emit(1)
        self.hide()

    def reject(self) -> None:
        self.finished.emit(0)
        self.hide()

    def _build_ui_legacy(self) -> None:
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_general_tab(), "通用")
        self.tabs.addTab(self._build_ocr_tab(), "OCR")
        self.tabs.addTab(self._build_translation_tab(), "翻译")
        self.tabs.addTab(self._build_overlay_tab(), "覆盖层")
        self.tabs.addTab(self._build_hotkey_tab(), "快捷键")
        layout.addWidget(self.tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 22)
        layout.setSpacing(18)

        header = QHBoxLayout()
        header.setSpacing(14)
        header.addWidget(SettingsGlyph())
        heading = QVBoxLayout()
        heading.setSpacing(3)
        title = QLabel("设置")
        title.setObjectName("SettingsTitle")
        subtitle = QLabel("按你的使用方式，安静地完成每一次翻译")
        subtitle.setObjectName("SettingsSubtitle")
        heading.addStretch(1)
        heading.addWidget(title)
        heading.addWidget(subtitle)
        heading.addStretch(1)
        header.addLayout(heading)
        header.addStretch(1)
        layout.addLayout(header)

        shell = QFrame()
        self._settings_shell = shell
        shell.setObjectName("SettingsShell")
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(12, 14, 16, 14)
        shell_layout.setSpacing(16)
        nav_host = QWidget()
        nav = QVBoxLayout(nav_host)
        nav.setContentsMargins(0, 0, 0, 0)
        nav.setSpacing(6)
        self.nav_buttons: list[QPushButton] = []
        for label in ("通用", "OCR", "翻译", "译文显示", "快捷键"):
            button = QPushButton(label)
            button.setObjectName("SettingsNavButton")
            button.setCheckable(True)
            button.setMinimumSize(148, 46)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda checked=False, b=button: self._select_page(self.nav_buttons.index(b)))
            self.nav_buttons.append(button)
            nav.addWidget(button)
        nav.addStretch(1)
        self._nav_indicator = QFrame(nav_host)
        self._nav_indicator.setObjectName("SettingsNavIndicator")
        self._nav_indicator.raise_()
        shell_layout.addWidget(nav_host)
        divider = QFrame()
        divider.setObjectName("SettingsDivider")
        divider.setFrameShape(QFrame.Shape.VLine)
        shell_layout.addWidget(divider)

        page_host = QFrame()
        page_host.setObjectName("SettingsPageHost")
        page_host_layout = QVBoxLayout(page_host)
        page_host_layout.setContentsMargins(0, 0, 0, 0)
        self.pages = QStackedWidget()
        self.pages.setObjectName("SettingsPages")
        for page in (
            self._build_general_tab(), self._build_ocr_tab(), self._build_translation_tab(),
            self._build_overlay_tab(), self._build_hotkey_tab(),
        ):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(page)
            self.pages.addWidget(scroll)
        page_host_layout.addWidget(self.pages)
        # A full-page curtain makes the section transition obvious, while
        # keeping the actual pages untouched (no opacity effects or reloads).
        self._page_sweep = QFrame(page_host)
        self._page_sweep.setObjectName("SettingsPageCurtain")
        self._page_sweep.hide()
        self._page_sweep.raise_()
        shell_layout.addWidget(page_host, 1)
        layout.addWidget(shell, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setObjectName("DialogCancelButton")
        self.btn_save = QPushButton("保存设置")
        self.btn_save.setObjectName("DialogSaveButton")
        for button in (self.btn_cancel, self.btn_save):
            button.setMinimumSize(116, 42)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.clicked.connect(self._on_cancel)
        self.btn_save.clicked.connect(self._on_accept)
        footer.addWidget(self.btn_cancel)
        footer.addWidget(self.btn_save)
        layout.addLayout(footer)
        self._select_page(0)

    def _select_page(self, index: int) -> None:
        previous = self._active_page
        for position, button in enumerate(self.nav_buttons):
            button.blockSignals(True)
            button.setChecked(position == index)
            button.blockSignals(False)
        QTimer.singleShot(0, lambda: self._move_nav_indicator(index))
        if index == previous:
            self.pages.setCurrentIndex(index)
            return
        self._active_page = index
        self._run_page_switch(index, index > previous)

    def _run_page_switch(self, index: int, moving_down: bool) -> None:
        """Switch behind a full-page sliding curtain without recreating widgets."""
        host = self._page_sweep.parentWidget()
        width = max(1, host.width())
        height = max(1, host.height())
        start_x = width if moving_down else -width
        if self._page_sweep_in is not None:
            self._page_sweep_in.stop()
        if self._page_sweep_out is not None:
            self._page_sweep_out.stop()
        self._page_sweep.setGeometry(start_x, 0, width, height)
        self._page_sweep.show()
        self._page_sweep_in = QPropertyAnimation(self._page_sweep, b"geometry", self)
        self._page_sweep_in.setDuration(190)
        self._page_sweep_in.setStartValue(self._page_sweep.geometry())
        self._page_sweep_in.setEndValue(self._page_sweep.geometry().translated(-start_x, 0))
        self._page_sweep_in.setEasingCurve(QEasingCurve.Type.OutCubic)

        def reveal_page() -> None:
            self.pages.setCurrentIndex(index)
            end_x = -width if moving_down else width
            self._page_sweep_out = QPropertyAnimation(self._page_sweep, b"geometry", self)
            self._page_sweep_out.setDuration(190)
            self._page_sweep_out.setStartValue(self._page_sweep.geometry())
            self._page_sweep_out.setEndValue(self._page_sweep.geometry().translated(end_x - self._page_sweep.x(), 0))
            self._page_sweep_out.setEasingCurve(QEasingCurve.Type.InCubic)
            self._page_sweep_out.finished.connect(self._page_sweep.hide)
            self._page_sweep_out.start()

        self._page_sweep_in.finished.connect(reveal_page)
        self._page_sweep_in.start()

    def _move_nav_indicator(self, index: int) -> None:
        button = self.nav_buttons[index]
        target = button.geometry().adjusted(0, 7, -button.width() + 4, -7)
        self._nav_indicator.setGeometry(target if self._nav_indicator.geometry().isNull() else self._nav_indicator.geometry())
        self._nav_animation = QPropertyAnimation(self._nav_indicator, b"geometry", self)
        self._nav_animation.setDuration(180)
        self._nav_animation.setStartValue(self._nav_indicator.geometry())
        self._nav_animation.setEndValue(target)
        self._nav_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._nav_animation.start()

    def _on_cancel(self) -> None:
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setText("返回中")
        QTimer.singleShot(150, self.reject)

    # ------------------------------------------------------------- tabs
    def _build_general_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
        self.chk_autostart = QCheckBox("开机自动启动")
        self.chk_tray = QCheckBox("关闭主窗口时最小化到托盘")
        self.chk_history = QCheckBox("保存截图与识别历史")
        self.history_dir = QLineEdit()
        pick = QPushButton("选择目录")
        pick.clicked.connect(self._pick_history_dir)
        row = QHBoxLayout()
        row.addWidget(self.history_dir, 1)
        row.addWidget(pick)

        form.addRow(self.chk_autostart)
        form.addRow(self.chk_tray)
        form.addRow(self.chk_history)
        form.addRow("历史目录", row)
        return tab

    def _build_ocr_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
        self.combo_engine = QComboBox()
        for engine in list_ocr_engines():
            self.combo_engine.addItem(engine, engine)
        self.combo_lang = QComboBox()
        for code, name in LANGUAGES[1:]:
            self.combo_lang.addItem(name, code)
        self.spin_confidence = QDoubleSpinBox()
        self.spin_confidence.setRange(0.0, 1.0)
        self.spin_confidence.setSingleStep(0.05)
        self.spin_y_tol = QDoubleSpinBox()
        self.spin_y_tol.setRange(0.0, 2.0)
        self.spin_y_tol.setSingleStep(0.1)
        self.spin_x_gap = QDoubleSpinBox()
        self.spin_x_gap.setRange(0.0, 5.0)
        self.spin_x_gap.setSingleStep(0.1)
        self.chk_gpu = QCheckBox("使用 GPU（需要带 GPU 的 PaddlePaddle）")
        self.chk_orientation = QCheckBox("启用文字方向识别")

        form.addRow("OCR 引擎", self.combo_engine)
        form.addRow("识别语言", self.combo_lang)
        form.addRow("最低置信度", self.spin_confidence)
        form.addRow("行合并容差", self.spin_y_tol)
        form.addRow("横向合并阈值", self.spin_x_gap)
        form.addRow(self.chk_gpu)
        form.addRow(self.chk_orientation)
        return tab

    def _build_translation_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        form = QFormLayout()
        self.combo_service = QComboBox()
        for service in list_translators():
            self.combo_service.addItem(service_display_name(service), service)
        self.combo_source = QComboBox()
        self.combo_target = QComboBox()
        for code, name in LANGUAGES:
            self.combo_source.addItem(name, code)
            self.combo_target.addItem(name, code)
        self.chk_keep_original = QCheckBox("译文上方同时保留原文")
        self.spin_timeout = QSpinBox()
        self.spin_timeout.setRange(5, 300)
        self.spin_retries = QSpinBox()
        self.spin_retries.setRange(0, 10)
        self.spin_interval = QDoubleSpinBox()
        self.spin_interval.setRange(0.0, 30.0)
        self.spin_interval.setSingleStep(0.1)

        form.addRow("翻译服务", self.combo_service)
        form.addRow("源语言", self.combo_source)
        form.addRow("目标语言", self.combo_target)
        form.addRow(self.chk_keep_original)
        form.addRow("超时（秒）", self.spin_timeout)
        form.addRow("失败重试次数", self.spin_retries)
        form.addRow("请求间隔（秒）", self.spin_interval)
        layout.addLayout(form)

        group = QGroupBox("在线翻译服务 API Key（优先读取环境变量 OPENAI_API_KEY / DEEPL_API_KEY / GOOGLE_TRANSLATE_API_KEY）")
        key_form = QFormLayout(group)
        self.edit_openai_key = QLineEdit()
        self.edit_openai_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit_openai_model = QLineEdit()
        self.edit_openai_url = QLineEdit()
        self.edit_deepl_key = QLineEdit()
        self.edit_deepl_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit_google_key = QLineEdit()
        self.edit_google_key.setEchoMode(QLineEdit.EchoMode.Password)
        key_form.addRow("OpenAI API Key", self.edit_openai_key)
        key_form.addRow("OpenAI 模型", self.edit_openai_model)
        key_form.addRow("OpenAI Base URL", self.edit_openai_url)
        key_form.addRow("DeepL API Key", self.edit_deepl_key)
        key_form.addRow("Google API Key", self.edit_google_key)
        layout.addWidget(group)

        privacy = QLabel(
            "隐私提示：使用在线翻译服务时，截图识别出的文字会发送到第三方服务器。"
            "本应用默认不保存截图、不上传图片，请自行确认服务商隐私政策。"
        )
        privacy.setObjectName("NoticeLabel")
        privacy.setWordWrap(True)
        privacy.setStyleSheet("color: #76756F; background: #F4F7FC; border: 1px solid #D9E6FA; border-radius: 8px; padding: 9px;")
        layout.addWidget(privacy)
        layout.addStretch(1)
        return tab

    def _build_overlay_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)

        self.combo_display_mode = QComboBox()
        self.combo_display_mode.addItem("仅显示译文", False)
        self.combo_display_mode.addItem("原文 + 译文（两行对照）", True)
        self.combo_font = QComboBox()
        self.combo_font.addItem("默认字体（自动选择，推荐）", "")
        self.combo_font.addItems(QFontDatabase.families())
        self.spin_font_size = QSpinBox()
        self.spin_font_size.setRange(6, 72)
        self.spin_min_font = QSpinBox()
        self.spin_min_font.setRange(4, 36)
        self.btn_text_color = QPushButton("选择颜色")
        self.btn_text_color.setFixedWidth(120)
        self.chk_auto_color = QCheckBox("自动识别文字颜色（推荐）")
        self.btn_bg_color = QPushButton("选择颜色")
        self.btn_bg_color.setFixedWidth(120)
        self.chk_auto_bg = QCheckBox("背景颜色随文字自动适配（推荐）")
        self.slider_bg_alpha = QSlider(Qt.Orientation.Horizontal)
        self.slider_bg_alpha.setRange(0, 255)
        self.spin_padding = QSpinBox()
        self.spin_padding.setRange(0, 32)
        self.spin_radius = QSpinBox()
        self.spin_radius.setRange(0, 32)
        self.chk_show_border = QCheckBox("显示文本框边框")
        self.combo_display_mode.currentIndexChanged.connect(self._sync_display_mode)

        form.addRow("字体", self.combo_font)
        form.addRow("译文显示形式", self.combo_display_mode)
        form.addRow("字号", self.spin_font_size)
        form.addRow("最小字号", self.spin_min_font)
        form.addRow("文字颜色", self.btn_text_color)
        form.addRow(self.chk_auto_color)
        form.addRow("背景颜色", self.btn_bg_color)
        form.addRow(self.chk_auto_bg)
        form.addRow("背景透明度", self.slider_bg_alpha)
        form.addRow("内边距", self.spin_padding)
        form.addRow("圆角", self.spin_radius)
        form.addRow(self.chk_show_border)
        return tab

    def _build_personalization_tab(self) -> QWidget:
        """Personal controls are grouped here so appearance choices stay discoverable."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(12)

        preview = QFrame()
        preview.setObjectName("PersonalizationPreview")
        preview_layout = QVBoxLayout(preview)
        preview_layout.setContentsMargins(18, 14, 18, 14)
        preview_title = QLabel("你的翻译工作台")
        preview_title.setObjectName("PersonalizationPreviewTitle")
        self.personalization_preview_caption = QLabel("暖白界面 · 舒适密度 · 平衡动效")
        self.personalization_preview_caption.setObjectName("PersonalizationPreviewCaption")
        preview_layout.addWidget(preview_title)
        preview_layout.addWidget(self.personalization_preview_caption)
        sample = QFrame()
        sample.setObjectName("TranslationSample")
        sample_layout = QVBoxLayout(sample)
        sample_layout.setContentsMargins(12, 10, 12, 10)
        self.sample_source = QLabel("Select a region to translate")
        self.sample_source.setObjectName("TranslationSampleSource")
        self.sample_target = QLabel("框选内容，即刻翻译")
        self.sample_target.setObjectName("TranslationSampleTarget")
        self.sample_chip = QLabel("实时预览")
        self.sample_chip.setObjectName("TranslationSampleChip")
        sample_layout.addWidget(self.sample_chip, 0, Qt.AlignmentFlag.AlignLeft)
        sample_layout.addWidget(self.sample_source)
        sample_layout.addWidget(self.sample_target)
        preview_layout.addWidget(sample)
        self.personalization_preview = preview
        layout.addWidget(preview)

        appearance = QGroupBox("界面外观")
        appearance_form = QFormLayout(appearance)
        self.combo_theme = QComboBox()
        self.combo_theme.addItem("暖白经典", "warm")
        self.combo_theme.addItem("云雾浅灰", "mist")
        self.combo_theme.addItem("夜航深色", "midnight")
        self.combo_background_style = QComboBox()
        self.combo_background_style.addItem("纸感暖白", "paper")
        self.combo_background_style.addItem("雾蓝留白", "mist")
        self.combo_background_style.addItem("纯净白", "clean")
        self.combo_ui_font = QComboBox()
        self.combo_ui_font.addItem("跟随系统（推荐）", "")
        self.combo_ui_font.addItems(QFontDatabase.families())
        self.btn_accent_color = QPushButton("选择强调色")
        self.btn_accent_color.setFixedWidth(136)
        appearance_form.addRow("界面主题", self.combo_theme)
        appearance_form.addRow("背景质感", self.combo_background_style)
        appearance_form.addRow("界面字体", self.combo_ui_font)
        appearance_form.addRow("强调色", self.btn_accent_color)
        layout.addWidget(appearance)

        rhythm = QGroupBox("布局与动效")
        rhythm_form = QFormLayout(rhythm)
        self.slider_ui_scale = QSlider(Qt.Orientation.Horizontal)
        self.slider_ui_scale.setRange(85, 125)
        self.label_ui_scale = QLabel()
        scale_row = QHBoxLayout()
        scale_row.addWidget(self.slider_ui_scale, 1)
        scale_row.addWidget(self.label_ui_scale)
        self.combo_density = QComboBox()
        self.combo_density.addItem("舒适", "comfortable")
        self.combo_density.addItem("紧凑", "compact")
        self.combo_density.addItem("宽松", "spacious")
        self.slider_corner_radius = QSlider(Qt.Orientation.Horizontal)
        self.slider_corner_radius.setRange(6, 24)
        self.label_corner_radius = QLabel()
        radius_row = QHBoxLayout()
        radius_row.addWidget(self.slider_corner_radius, 1)
        radius_row.addWidget(self.label_corner_radius)
        self.chk_motion_enabled = QCheckBox("启用页面切换与按钮反馈动画")
        self.combo_motion_level = QComboBox()
        self.combo_motion_level.addItem("克制", "subtle")
        self.combo_motion_level.addItem("平衡（推荐）", "balanced")
        self.combo_motion_level.addItem("鲜明", "expressive")
        self.chk_reduce_transparency = QCheckBox("减少透明与柔光效果")
        rhythm_form.addRow("界面缩放", scale_row)
        rhythm_form.addRow("信息密度", self.combo_density)
        rhythm_form.addRow("圆角大小", radius_row)
        rhythm_form.addRow("动效风格", self.combo_motion_level)
        rhythm_form.addRow(self.chk_motion_enabled)
        rhythm_form.addRow(self.chk_reduce_transparency)
        layout.addWidget(rhythm)
        layout.addStretch(1)

        self.btn_accent_color.clicked.connect(self._pick_accent_color)
        self.slider_ui_scale.valueChanged.connect(self._refresh_personalization_preview)
        self.slider_corner_radius.valueChanged.connect(self._refresh_personalization_preview)
        self.combo_theme.currentIndexChanged.connect(self._refresh_personalization_preview)
        self.combo_density.currentIndexChanged.connect(self._refresh_personalization_preview)
        self.combo_motion_level.currentIndexChanged.connect(self._refresh_personalization_preview)
        return tab

    def _build_hotkey_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
        self.hotkey_edits: dict[str, QKeySequenceEdit] = {}
        for action, label in _HOTKEY_ACTIONS:
            editor = QKeySequenceEdit()
            self.hotkey_edits[action] = editor
            form.addRow(label, editor)
        return tab

    # ------------------------------------------------------------- load/save
    def _load(self) -> None:
        cfg = self.config
        self._set_combo(self.combo_display_mode, bool(cfg.get("translation.keep_original", False)))
        self.chk_autostart.setChecked(bool(cfg.get("general.startup_with_system", False)))
        self.chk_tray.setChecked(bool(cfg.get("general.minimize_to_tray", True)))
        self.chk_history.setChecked(bool(cfg.get("general.save_history", False)))
        self.history_dir.setText(str(cfg.get("general.history_dir", "")))

        self._set_combo(self.combo_engine, cfg.get("ocr.engine", "paddle"))
        self._set_combo(self.combo_lang, cfg.get("ocr.lang", "ch"))
        self.spin_confidence.setValue(float(cfg.get("ocr.min_confidence", 0.6)))
        self.spin_y_tol.setValue(float(cfg.get("ocr.merge_y_tolerance_ratio", 0.5)))
        self.spin_x_gap.setValue(float(cfg.get("ocr.merge_x_gap_ratio", 0.8)))
        self.chk_gpu.setChecked(bool(cfg.get("ocr.paddle.use_gpu", False)))
        self.chk_orientation.setChecked(bool(cfg.get("ocr.paddle.use_textline_orientation", True)))

        self._set_combo(self.combo_service, cfg.get("translation.service", "mock"))
        self._set_combo(self.combo_source, cfg.get("translation.source_language", "auto"))
        self._set_combo(self.combo_target, cfg.get("translation.target_language", "zh"))
        self.chk_keep_original.setChecked(bool(cfg.get("translation.keep_original", False)))
        self.spin_timeout.setValue(int(cfg.get("translation.timeout_seconds", 30)))
        self.spin_retries.setValue(int(cfg.get("translation.max_retries", 3)))
        self.spin_interval.setValue(float(cfg.get("translation.request_interval_seconds", 0.0)))
        self.edit_openai_key.setText(str(cfg.get("translation.openai.api_key", "")))
        self.edit_openai_model.setText(str(cfg.get("translation.openai.model", "gpt-4o-mini")))
        self.edit_openai_url.setText(str(cfg.get("translation.openai.base_url", "https://api.openai.com/v1")))
        self.edit_deepl_key.setText(str(cfg.get("translation.deepl.api_key", "")))
        self.edit_google_key.setText(str(cfg.get("translation.google.api_key", "")))

        self._set_combo(self.combo_font, cfg.get("overlay.font_family", ""))
        self.spin_font_size.setValue(int(cfg.get("overlay.font_size", 18)))
        self.spin_min_font.setValue(int(cfg.get("overlay.min_font_size", 8)))
        self._text_color = QColor(str(cfg.get("overlay.text_color", "#FFFFFF")))
        self._bg_color = QColor(str(cfg.get("overlay.background_color", "#000000")))
        self._update_color_buttons()
        self.chk_auto_color.setChecked(bool(cfg.get("overlay.use_auto_text_color", True)))
        self.chk_auto_bg.setChecked(bool(cfg.get("overlay.auto_background", True)))
        self.slider_bg_alpha.setValue(int(cfg.get("overlay.background_alpha", 160)))
        self.spin_padding.setValue(int(cfg.get("overlay.padding", 4)))
        self.spin_radius.setValue(int(cfg.get("overlay.border_radius", 4)))
        self.chk_show_border.setChecked(bool(cfg.get("overlay.show_border", True)))

        for action, _ in _HOTKEY_ACTIONS:
            self.hotkey_edits[action].setKeySequence(QKeySequence(str(cfg.get(f"hotkeys.{action}", ""))))

        self.btn_text_color.clicked.connect(lambda: self._pick_color("text"))
        self.btn_bg_color.clicked.connect(lambda: self._pick_color("bg"))

    def _on_accept(self) -> None:
        """A short, deterministic save acknowledgement; no page compositing involved."""
        self.btn_save.setEnabled(False)
        self.btn_save.setText("已保存")
        QTimer.singleShot(220, self._commit_accept)

    def _commit_accept(self) -> None:
        cfg = self.config
        cfg.set("general.startup_with_system", self.chk_autostart.isChecked())
        cfg.set("general.minimize_to_tray", self.chk_tray.isChecked())
        cfg.set("general.save_history", self.chk_history.isChecked())
        cfg.set("general.history_dir", self.history_dir.text().strip())

        cfg.set("ocr.engine", self.combo_engine.currentData() or "paddle")
        cfg.set("ocr.lang", self.combo_lang.currentData() or "ch")
        cfg.set("ocr.min_confidence", self.spin_confidence.value())
        cfg.set("ocr.merge_y_tolerance_ratio", self.spin_y_tol.value())
        cfg.set("ocr.merge_x_gap_ratio", self.spin_x_gap.value())
        cfg.set("ocr.paddle.use_gpu", self.chk_gpu.isChecked())
        cfg.set("ocr.paddle.use_textline_orientation", self.chk_orientation.isChecked())

        cfg.set("translation.service", self.combo_service.currentData() or "mock")
        cfg.set("translation.source_language", self.combo_source.currentData() or "auto")
        cfg.set("translation.target_language", self.combo_target.currentData() or "zh")
        cfg.set("translation.keep_original", self.chk_keep_original.isChecked())
        cfg.set("translation.timeout_seconds", self.spin_timeout.value())
        cfg.set("translation.max_retries", self.spin_retries.value())
        cfg.set("translation.request_interval_seconds", self.spin_interval.value())
        cfg.set("translation.openai.api_key", self.edit_openai_key.text().strip())
        cfg.set("translation.openai.model", self.edit_openai_model.text().strip())
        cfg.set("translation.openai.base_url", self.edit_openai_url.text().strip())
        cfg.set("translation.deepl.api_key", self.edit_deepl_key.text().strip())
        cfg.set("translation.google.api_key", self.edit_google_key.text().strip())

        cfg.set("overlay.font_family", self.combo_font.currentData() or "")
        cfg.set("translation.keep_original", self.combo_display_mode.currentData() or False)
        cfg.set("overlay.font_size", self.spin_font_size.value())
        cfg.set("overlay.min_font_size", self.spin_min_font.value())
        cfg.set("overlay.text_color", self._text_color.name())
        cfg.set("overlay.use_auto_text_color", self.chk_auto_color.isChecked())
        cfg.set("overlay.background_color", self._bg_color.name())
        cfg.set("overlay.auto_background", self.chk_auto_bg.isChecked())
        cfg.set("overlay.background_alpha", self.slider_bg_alpha.value())
        cfg.set("overlay.padding", self.spin_padding.value())
        cfg.set("overlay.border_radius", self.spin_radius.value())
        cfg.set("overlay.show_border", self.chk_show_border.isChecked())

        hotkeys: dict[str, str] = {}
        for action, _ in _HOTKEY_ACTIONS:
            hotkeys[action] = normalize_hotkey(self.hotkey_edits[action].keySequence().toString())
        seen: dict[str, str] = {}
        for action, combo in hotkeys.items():
            if not combo:
                continue
            if combo in seen:
                QMessageBox.warning(self, "快捷键冲突", f"{combo} 被重复绑定（{seen[combo]} / {action}）")
                return
            seen[combo] = action
        for action, combo in hotkeys.items():
            cfg.set(f"hotkeys.{action}", combo)

        cfg.save()
        self.accept()

    # ------------------------------------------------------------- helpers
    def _set_combo(self, combo: QComboBox, value: str) -> None:
        idx = combo.findData(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _pick_history_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择历史记录目录", self.history_dir.text())
        if directory:
            self.history_dir.setText(directory)

    def _pick_color(self, target: str) -> None:
        if target == "text":
            color = QColorDialog.getColor(self._text_color, self, "选择文字颜色")
            if color.isValid():
                self._text_color = color
        else:
            color = QColorDialog.getColor(self._bg_color, self, "选择背景颜色")
            if color.isValid():
                self._bg_color = color
        self._update_color_buttons()

    def _update_color_buttons(self) -> None:
        self.btn_text_color.setStyleSheet(f"background-color: {self._text_color.name()}; color: {self._text_color.name()};")
        self.btn_bg_color.setStyleSheet(f"background-color: {self._bg_color.name()}; color: {self._bg_color.name()};")

    def _pick_accent_color(self) -> None:
        color = QColorDialog.getColor(self._accent_color, self, "选择强调色")
        if color.isValid():
            self._accent_color = color
            self._refresh_personalization_preview()

    def _refresh_personalization_preview(self, *_args) -> None:
        """Instant preview keeps customization tangible before saving it."""
        if not hasattr(self, "_accent_color"):
            return
        accent = self._accent_color.name()
        radius = self.slider_corner_radius.value()
        scale = self.slider_ui_scale.value()
        density = self.combo_density.currentText()
        motion = self.combo_motion_level.currentText()
        theme = self.combo_theme.currentData() or "warm"
        background = {"warm": "#FFFEFC", "mist": "#F2F6FC", "midnight": "#252A33"}[theme]
        text = "#FFFFFF" if theme == "midnight" else "#292D2A"
        muted = "#C9D0DC" if theme == "midnight" else "#6F716C"
        self.btn_accent_color.setStyleSheet(
            f"background: {accent}; color: #FFFFFF; border: 1px solid {accent};"
        )
        self.personalization_preview.setStyleSheet(
            f"background: {background}; border: 1px solid {accent}; border-radius: {radius}px;"
        )
        self.personalization_preview.findChild(QLabel, "PersonalizationPreviewTitle").setStyleSheet(
            f"color: {text}; font-size: {max(14, round(16 * scale / 100))}px; font-weight: 700;"
        )
        self.personalization_preview_caption.setStyleSheet(f"color: {muted};")
        self.sample_source.setStyleSheet(f"color: {muted}; font-size: 12px; border: none;")
        self.sample_target.setStyleSheet(f"color: {text}; font-size: {max(14, round(16 * scale / 100))}px; font-weight: 600; border: none;")
        self.sample_chip.setStyleSheet(
            f"color: {accent}; background: {accent}18; border: none; border-radius: 7px; padding: 3px 7px;"
        )
        self.personalization_preview.findChild(QFrame, "TranslationSample").setStyleSheet(
            f"background: {('#303946' if theme == 'midnight' else '#FFFFFF')}; border: 1px solid {accent}33; border-radius: {max(8, radius - 2)}px;"
        )
        self.personalization_preview_caption.setText(f"{density}密度 · {motion}动效 · {scale}% 缩放")
        self.label_ui_scale.setText(f"{scale}%")
        self.label_corner_radius.setText(f"{radius}px")

    def _sync_display_mode(self) -> None:
        """覆盖层"译文显示形式"与翻译页"保留原文"保持同步。"""
        self.chk_keep_original.blockSignals(True)
        self.chk_keep_original.setChecked(bool(self.combo_display_mode.currentData()))
        self.chk_keep_original.blockSignals(False)
