"""设置对话框：通用 / 个性化 / OCR / 翻译 / 覆盖层 / 快捷键。"""

from __future__ import annotations

import copy
import sys
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QPropertyAnimation, QRect, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QScrollArea,
    QProgressBar,
    QFrame,
    QStackedWidget,
    QVBoxLayout,
    QCheckBox,
    QApplication,
    QWidget,
)

from app.config import DEFAULTS, AppConfig
from app.hotkeys import HotkeyError, normalize_hotkey
from app.logger import app_data_dir, get_logger
from app.version import __version__
from services.diagnostics import DiagnosticsExportError, export_diagnostics
from services.ocr.base import list_ocr_engines
from services.ocr.paddle_ocr import PaddleOCREngine, component_manager
from services.translation.base import list_translators
from services.translation.factory import service_display_name
from ui.appearance import (
    ACCENT_PRESETS,
    DENSITY_PRESETS,
    MOTION_PRESETS,
    PALETTE_PRESETS,
    SURFACE_PRESETS,
    current_tokens,
)
from ui.motion import BASE, SLOW, ENTER_EASING, EXIT_EASING, MOVE_EASING, motion_duration
from ui.ocr_component_tasks import PaddleComponentInstallTask
from ui.personalization import AppearancePreview, PersonalizationChoiceRow
from ui.update_tasks import UpdateCheckTask, UpdateDownloadTask
from utils.language_utils import LANGUAGES

log = get_logger("settings")

_HOTKEY_ACTIONS = [
    ("capture_region", "框选翻译"),
    ("capture_fullscreen", "全屏翻译"),
    ("capture_window", "当前窗口翻译"),
    ("toggle_overlay", "隐藏/显示译文"),
    ("refresh", "重新识别翻译"),
]


class SettingsGlyph(QLabel):
    """The canonical application icon, reused without redrawing it."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(58, 58)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
        self._icon_source = root / "assets" / "app_launch_v4.png"
        source = QPixmap(str(self._icon_source))
        if not source.isNull():
            self.setPixmap(
                source.scaled(
                    self.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        self.setAccessibleName("屏幕翻译")

    def set_lifecycle_progress(self, _progress: float) -> None:
        """Keep the canonical mark still while the surrounding page moves."""


class SettingsDialog(QWidget):
    accepted = Signal()
    exit_requested = Signal(int)
    finished = Signal(int)
    install_update_requested = Signal(str, str)
    def __init__(self, config: AppConfig, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.setObjectName("SettingsDialog")
        self.setWindowTitle("设置")
        self.setMinimumSize(900, 610)
        self.resize(1020, 680)
        self._page_sweep_in: QPropertyAnimation | None = None
        self._page_sweep_out: QPropertyAnimation | None = None
        self._active_page = 0
        self._embedded = False
        self._nav_initialized = False
        self._pending_page = 0
        self._queued_page: int | None = None
        self._page_transition_active = False
        self._sweep_moving_down = True
        self._exit_pending = False
        self._pending_exit_result = -1
        self._close_intent = -1
        self._lifecycle_progress = 0.0
        self._update_info = None
        self._downloaded_update: tuple[str, str] | None = None
        self._update_check_task: UpdateCheckTask | None = None
        self._update_download_task: UpdateDownloadTask | None = None
        self._paddle_component_task: PaddleComponentInstallTask | None = None
        self._build_ui()
        self._load()
        self.refresh_appearance()

    def refresh_appearance(self) -> None:
        tokens = current_tokens()
        for button in self.nav_buttons:
            button.setMinimumHeight(tokens.nav_height)
            button.setMaximumHeight(tokens.nav_height)
        for card in self.findChildren(QFrame):
            if card.objectName() != "SettingsCard" or card.layout() is None:
                continue
            padding = tokens.card_padding
            card.layout().setContentsMargins(padding, max(12, padding - 4), padding, padding)
        for page_index in range(self.pages.count()):
            scroll = self.pages.widget(page_index)
            page = scroll.widget() if isinstance(scroll, QScrollArea) else None
            if page is not None and page.layout() is not None:
                page.layout().setSpacing(tokens.page_spacing)
        self._page_sweep.update()
        self._settings_glyph.update()
        self.update()

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
        if self._exit_pending:
            return
        self._commit_timer.stop()
        self.accepted.emit()
        self._request_exit(1)

    def reject(self) -> None:
        if self._exit_pending:
            return
        self._commit_timer.stop()
        self._save_close_timer.stop()
        self._pending_hotkeys = None
        self._request_exit(0)

    @property
    def exit_pending(self) -> bool:
        return self._exit_pending

    @property
    def pending_exit_result(self) -> int:
        return self._pending_exit_result

    @property
    def close_intent(self) -> int:
        """Requested result, including the short saved-confirmation hold."""
        return self._close_intent

    def set_lifecycle_progress(self, progress: float) -> None:
        self._lifecycle_progress = max(0.0, min(1.0, float(progress)))
        self._settings_glyph.set_lifecycle_progress(self._lifecycle_progress)

    def _request_exit(self, result: int) -> None:
        self.cancel_background_tasks()
        self._settle_page_transition_for_exit()
        self._exit_pending = True
        self._pending_exit_result = int(result)
        self._close_intent = int(result)
        self.btn_cancel.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.exit_requested.emit(int(result))

    def complete_exit(self, result: int) -> None:
        if not self._exit_pending or int(result) != self._pending_exit_result:
            return
        self._exit_pending = False
        self._pending_exit_result = -1
        self._close_intent = -1
        self.finished.emit(int(result))
        self.hide()

    def cancel_exit(self) -> bool:
        if not self._exit_pending or self._pending_exit_result != 0:
            return False
        self._exit_pending = False
        self._pending_exit_result = -1
        self._close_intent = -1
        self._settings_shell.setEnabled(True)
        self.btn_cancel.setEnabled(True)
        self.btn_save.setEnabled(True)
        self.btn_cancel.setText("取消")
        self.btn_save.setProperty("saved", False)
        self.btn_save.setText("保存设置")
        self.btn_save.style().unpolish(self.btn_save)
        self.btn_save.style().polish(self.btn_save)
        return True

    def _settle_page_transition_for_exit(self) -> None:
        self._page_sweep_in.stop()
        self._page_sweep_out.stop()
        self._page_sweep.hide()
        self._page_transition_active = False
        self._queued_page = None
        current = self.pages.currentIndex()
        self._active_page = current
        self._pending_page = current
        self._pending_nav_index = current
        self._nav_move_timer.stop()
        for position, button in enumerate(self.nav_buttons):
            button.blockSignals(True)
            button.setChecked(position == current)
            button.blockSignals(False)
        self._move_nav_indicator(current)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(14)
        self._settings_glyph = SettingsGlyph()
        self._settings_glyph.set_lifecycle_progress(self._lifecycle_progress)
        header.addWidget(self._settings_glyph)
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
        shell_layout.setContentsMargins(10, 12, 14, 12)
        shell_layout.setSpacing(14)
        nav_host = QWidget()
        nav = QVBoxLayout(nav_host)
        nav.setContentsMargins(0, 0, 0, 0)
        nav.setSpacing(6)
        self.nav_buttons: list[QPushButton] = []
        for label in ("通用", "个性化", "OCR", "翻译", "译文显示", "快捷键"):
            button = QPushButton(label)
            button.setObjectName("SettingsNavButton")
            button.setCheckable(True)
            button.setMinimumSize(144, 42)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda checked=False, b=button: self._select_page(self.nav_buttons.index(b)))
            self.nav_buttons.append(button)
            nav.addWidget(button)
        nav.addStretch(1)
        self._nav_indicator = QFrame(nav_host)
        self._nav_indicator.setObjectName("SettingsNavIndicator")
        self._nav_indicator.raise_()
        self._nav_animation = QPropertyAnimation(self._nav_indicator, b"geometry", self)
        self._nav_animation.setDuration(SLOW)
        self._nav_animation.setEasingCurve(MOVE_EASING)
        self._pending_nav_index = 0
        self._nav_move_timer = QTimer(self)
        self._nav_move_timer.setSingleShot(True)
        self._nav_move_timer.timeout.connect(self._move_pending_nav_indicator)
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
            self._build_general_tab(), self._build_personalization_tab(),
            self._build_ocr_tab(), self._build_translation_tab(),
            self._build_overlay_tab(), self._build_hotkey_tab(),
        ):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(page)
            self.pages.addWidget(scroll)
        page_host_layout.addWidget(self.pages)
        # Preserve the original full-page curtain: it covers the old page,
        # hands over at center, then exits in the navigation direction.
        self._page_sweep = QFrame(page_host)
        self._page_sweep.setObjectName("SettingsPageCurtain")
        self._page_sweep.hide()
        self._page_sweep.raise_()
        self._page_sweep_in = QPropertyAnimation(self._page_sweep, b"geometry", self)
        self._page_sweep_in.setDuration(BASE)
        self._page_sweep_in.setEasingCurve(ENTER_EASING)
        self._page_sweep_in.finished.connect(self._reveal_sweep_page)
        self._page_sweep_out = QPropertyAnimation(self._page_sweep, b"geometry", self)
        self._page_sweep_out.setDuration(BASE)
        self._page_sweep_out.setEasingCurve(EXIT_EASING)
        self._page_sweep_out.finished.connect(self._finish_page_switch)
        shell_layout.addWidget(page_host, 1)
        layout.addWidget(shell, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(10)
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
        self._save_animation = QPropertyAnimation(self.btn_save, b"minimumWidth", self)
        self._save_animation.setDuration(SLOW)
        self._save_animation.setEasingCurve(ENTER_EASING)
        self._save_close_timer = QTimer(self)
        self._save_close_timer.setSingleShot(True)
        self._save_close_timer.timeout.connect(self.accept)
        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.timeout.connect(self._commit_pending)
        self._pending_hotkeys: dict[str, str] | None = None
        footer.addWidget(self.btn_cancel)
        footer.addWidget(self.btn_save)
        layout.addLayout(footer)
        self.combo_display_mode.currentIndexChanged.connect(self._sync_display_mode)
        self.chk_keep_original.toggled.connect(self._sync_keep_original)
        self._select_page(0)

    def _select_page(self, index: int) -> None:
        for position, button in enumerate(self.nav_buttons):
            button.blockSignals(True)
            button.setChecked(position == index)
            button.blockSignals(False)
        self._pending_nav_index = index
        self._nav_move_timer.start(0)

        if self._page_transition_active:
            # Keep one full curtain in flight. Re-clicking its target must not
            # expose the new page early; a different target becomes the next
            # transition and is measured from the page actually revealed.
            self._queued_page = None if index == self._pending_page else index
            return

        self._active_page = self.pages.currentIndex()
        if index == self._active_page:
            return
        self._run_page_switch(index, index > self._active_page)

    def _run_page_switch(self, index: int, moving_down: bool) -> None:
        """Switch behind the original full-page sliding curtain."""
        enter_duration = motion_duration(BASE, large_surface=True)
        if enter_duration == 0:
            self._page_sweep_in.stop()
            self._page_sweep_out.stop()
            self.pages.setCurrentIndex(index)
            self._active_page = index
            self._pending_page = index
            self._queued_page = None
            self._page_transition_active = False
            self._page_sweep.hide()
            return
        host = self._page_sweep.parentWidget()
        width = max(1, host.width())
        height = max(1, host.height())
        start_x = width if moving_down else -width
        self._page_sweep_in.stop()
        self._page_sweep_out.stop()
        self._pending_page = index
        self._page_transition_active = True
        self._sweep_moving_down = moving_down
        start = QRect(start_x, 0, width, height)
        covered = QRect(0, 0, width, height)
        self._page_sweep.setGeometry(start)
        self._page_sweep.show()
        self._page_sweep.raise_()
        self._page_sweep_in.setDuration(enter_duration)
        self._page_sweep_in.setStartValue(start)
        self._page_sweep_in.setEndValue(covered)
        self._page_sweep_in.start()

    def _reveal_sweep_page(self) -> None:
        self.pages.setCurrentIndex(self._pending_page)
        self._active_page = self._pending_page
        host = self._page_sweep.parentWidget()
        width = max(1, host.width())
        height = max(1, host.height())
        end_x = -width if self._sweep_moving_down else width
        self._page_sweep_out.setDuration(motion_duration(BASE, large_surface=True))
        self._page_sweep_out.setStartValue(QRect(0, 0, width, height))
        self._page_sweep_out.setEndValue(QRect(end_x, 0, width, height))
        self._page_sweep_out.start()

    def _finish_page_switch(self) -> None:
        self._page_sweep.hide()
        self._page_transition_active = False
        self._active_page = self.pages.currentIndex()
        queued_page = self._queued_page
        self._queued_page = None
        if queued_page is None or queued_page == self._active_page:
            return
        self._run_page_switch(queued_page, queued_page > self._active_page)

    def _move_nav_indicator(self, index: int) -> None:
        button = self.nav_buttons[index]
        target = button.geometry().adjusted(0, 7, -button.width() + 4, -7)
        if not self._nav_initialized:
            self._nav_initialized = True
            self._nav_indicator.setGeometry(target)
            self._nav_indicator.show()
            return
        self._nav_animation.stop()
        duration = motion_duration(SLOW)
        if duration == 0:
            self._nav_indicator.setGeometry(target)
            self._nav_indicator.show()
            return
        self._nav_animation.setDuration(duration)
        start = self._nav_indicator.geometry()
        top = min(start.top(), target.top())
        bottom = max(start.bottom(), target.bottom())
        stretched = QRect(target.x(), top, target.width(), bottom - top + 1)
        self._nav_animation.setKeyValues(
            [(0.0, start), (0.45, stretched), (0.62, stretched), (1.0, target)]
        )
        self._nav_animation.start()

    def _move_pending_nav_indicator(self) -> None:
        self._move_nav_indicator(self._pending_nav_index)

    def _on_cancel(self) -> None:
        self._commit_timer.stop()
        self._save_close_timer.stop()
        self._pending_hotkeys = None
        self.btn_cancel.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.btn_cancel.setText("返回中")
        self.reject()

    # ------------------------------------------------------------- tabs
    @staticmethod
    def _new_page() -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 6, 10, 10)
        layout.setSpacing(12)
        return page, layout

    @staticmethod
    def _new_form() -> QFormLayout:
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(22)
        form.setVerticalSpacing(10)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        return form

    @staticmethod
    def _card(title: str, description: str, body: QLayout) -> QFrame:
        card = QFrame()
        card.setObjectName("SettingsCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(10)
        title_label = QLabel(title)
        title_label.setObjectName("SettingsCardTitle")
        description_label = QLabel(description)
        description_label.setObjectName("SettingsCardDescription")
        description_label.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(description_label)
        layout.addSpacing(2)
        layout.addLayout(body)
        return card

    def _build_general_tab(self) -> QWidget:
        tab, layout = self._new_page()
        self.chk_autostart = QCheckBox("开机自动启动")
        self.chk_tray = QCheckBox("关闭主窗口时最小化到托盘")
        self.chk_history = QCheckBox("保存截图与识别历史")
        startup_form = self._new_form()
        startup_form.addRow(self.chk_autostart)
        startup_form.addRow(self.chk_tray)
        layout.addWidget(self._card("启动与后台", "决定应用如何进入工作状态与停留在系统托盘。", startup_form))

        self.history_dir = QLineEdit()
        pick = QPushButton("选择目录")
        pick.clicked.connect(self._pick_history_dir)
        row = QHBoxLayout()
        row.addWidget(self.history_dir, 1)
        row.addWidget(pick)
        history_form = self._new_form()
        history_form.addRow(self.chk_history)
        history_form.addRow("历史目录", row)
        layout.addWidget(self._card("本地记录", "历史记录只保存在你选择的本机目录。", history_form))

        self.chk_auto_updates = QCheckBox("自动检查新版本")
        self.update_status = QLabel(f"当前版本 {__version__}")
        self.update_status.setWordWrap(True)
        self.btn_check_update = QPushButton("检查更新")
        self.btn_check_update.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_progress = QProgressBar()
        self.update_progress.setTextVisible(False)
        self.update_progress.setRange(0, 100)
        self.update_progress.setValue(0)
        self.update_progress.hide()
        self.btn_check_update.clicked.connect(self._on_update_action)
        update_actions = QHBoxLayout()
        update_actions.setContentsMargins(0, 0, 0, 0)
        update_actions.addWidget(self.update_status, 1)
        update_actions.addWidget(self.btn_check_update)
        update_body = QVBoxLayout()
        update_body.setContentsMargins(0, 0, 0, 0)
        update_body.setSpacing(10)
        update_body.addWidget(self.chk_auto_updates)
        update_body.addLayout(update_actions)
        update_body.addWidget(self.update_progress)
        layout.addWidget(
            self._card(
                "软件更新",
                "从 GitHub Releases 获取轻量安装包，下载后必须通过 SHA-256 校验。",
                update_body,
            )
        )

        self.btn_export_diagnostics = QPushButton("导出诊断日志")
        self.btn_export_diagnostics.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_export_diagnostics.clicked.connect(self._export_diagnostics)
        diagnostics_body = QHBoxLayout()
        diagnostics_body.setContentsMargins(0, 0, 0, 0)
        diagnostics_body.addWidget(QLabel("仅包含脱敏配置、运行环境和最近日志。"), 1)
        diagnostics_body.addWidget(self.btn_export_diagnostics)
        layout.addWidget(
            self._card(
                "诊断与支持",
                "不会打包截图、翻译历史、模型文件或 API Key。",
                diagnostics_body,
            )
        )
        layout.addStretch(1)
        return tab

    def _export_diagnostics(self) -> None:
        suggested = app_data_dir() / f"ScreenTranslator-diagnostics-{time.strftime('%Y%m%d-%H%M%S')}.zip"
        target, _ = QFileDialog.getSaveFileName(
            self, "导出诊断日志", str(suggested), "ZIP 压缩包 (*.zip)"
        )
        if not target:
            return
        try:
            exported = export_diagnostics(target, self.config, app_version=__version__)
        except DiagnosticsExportError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        QMessageBox.information(self, "导出完成", f"诊断日志已保存到：\n{exported}")

    def _on_update_action(self) -> None:
        if self._downloaded_update is not None:
            self._confirm_update_install()
        elif self._update_info is None:
            self._check_for_updates()
        else:
            self._download_update()

    def _check_for_updates(self) -> None:
        if self._update_check_task is not None and self._update_check_task.isRunning():
            return
        self.btn_check_update.setEnabled(False)
        self.btn_check_update.setText("检查中…")
        self.update_status.setText("正在连接 GitHub Releases…")
        task = UpdateCheckTask(
            __version__,
            str(self.config.get("updates.repository", "nimbus-translate/screen-translator")),
            include_prereleases=bool(self.config.get("updates.include_prereleases", False)),
            parent=QApplication.instance(),
        )
        self._update_check_task = task
        task.updateFound.connect(self._update_found)
        task.upToDate.connect(self._update_current)
        task.failed.connect(self._update_failed)
        task.finished.connect(lambda current=task: self._update_task_finished("check", current))
        task.finished.connect(task.deleteLater)
        task.start()

    def _update_found(self, info) -> None:
        self._update_info = info
        self._downloaded_update = None
        self.update_status.setText(f"发现新版本 {info.latest_version} · {info.release_name}")
        self.btn_check_update.setText("下载更新")
        self.btn_check_update.setEnabled(True)

    def _update_current(self) -> None:
        self._update_info = None
        self.update_status.setText(f"当前已是最新版本 {__version__}")
        self.btn_check_update.setText("重新检查")
        self.btn_check_update.setEnabled(True)

    def _update_failed(self, message: str, operation: str = "检查") -> None:
        self.update_progress.hide()
        self.update_status.setText(f"{operation}失败：{message}")
        self.btn_check_update.setText("重试")
        self.btn_check_update.setEnabled(True)

    def _download_update(self) -> None:
        info = self._update_info
        if info is None or (
            self._update_download_task is not None and self._update_download_task.isRunning()
        ):
            return
        destination = app_data_dir() / "updates" / info.asset.name
        self.btn_check_update.setEnabled(False)
        self.btn_check_update.setText("下载中…")
        self.update_progress.setValue(0)
        self.update_progress.show()
        task = UpdateDownloadTask(
            info,
            destination,
            str(self.config.get("updates.repository", "nimbus-translate/screen-translator")),
            parent=QApplication.instance(),
        )
        self._update_download_task = task
        task.progress.connect(self._update_download_progress)
        task.completed.connect(self._update_downloaded)
        task.failed.connect(self._update_download_failed)
        task.finished.connect(lambda current=task: self._update_task_finished("download", current))
        task.finished.connect(task.deleteLater)
        task.start()

    def _update_download_progress(self, completed: int, total: int) -> None:
        if total > 0:
            percent = max(0, min(100, round(completed * 100 / total)))
            self.update_progress.setRange(0, 100)
            self.update_progress.setValue(percent)
            self.update_status.setText(f"正在下载并校验… {percent}%")
        else:
            self.update_progress.setRange(0, 0)

    def _update_download_failed(self, message: str) -> None:
        self._update_failed(message, "下载")

    def _update_downloaded(self, path: str, sha256: str) -> None:
        self._downloaded_update = (path, sha256)
        self.update_progress.setRange(0, 100)
        self.update_progress.setValue(100)
        self.update_status.setText("更新包已下载并通过 SHA-256 校验")
        self.btn_check_update.setText("安装已下载更新")
        self.btn_check_update.setEnabled(True)
        self._confirm_update_install()

    def _confirm_update_install(self) -> None:
        downloaded = self._downloaded_update
        if downloaded is None:
            return
        answer = QMessageBox.question(
            self,
            "安装更新",
            "更新包已通过 SHA-256 校验。确认后还会验证发布者数字签名；"
            "签名有效才会退出 ScreenTranslator 并启动安装程序。",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.btn_check_update.setEnabled(False)
            self.install_update_requested.emit(*downloaded)

    def cancel_background_tasks(self) -> None:
        """Request cancellation without destroying a still-running QThread."""
        for task in (
            self._update_check_task,
            self._update_download_task,
            self._paddle_component_task,
        ):
            if task is not None and task.isRunning():
                task.cancel()

    def _update_task_finished(self, kind: str, task) -> None:
        if kind == "check" and self._update_check_task is task:
            self._update_check_task = None
        elif kind == "download" and self._update_download_task is task:
            self._update_download_task = None

    def _build_personalization_tab(self) -> QWidget:
        tab, layout = self._new_page()

        self.palette_choices = PersonalizationChoiceRow(
            list(PALETTE_PRESETS), mode="palette"
        )
        palette_body = QVBoxLayout()
        palette_body.setContentsMargins(0, 0, 0, 0)
        palette_body.addWidget(self.palette_choices)
        layout.addWidget(
            self._card(
                "主题氛围",
                "每套配色都围绕暖白、深灰线稿与短下划线重新校准。",
                palette_body,
            )
        )

        accent_specs = [(value, label, "") for value, label in ACCENT_PRESETS]
        self.accent_choices = PersonalizationChoiceRow(accent_specs, mode="accent")
        accent_body = QVBoxLayout()
        accent_body.setContentsMargins(0, 0, 0, 0)
        accent_body.addWidget(self.accent_choices)
        layout.addWidget(
            self._card(
                "界面强调色",
                "同步改变按钮、状态、短线反馈与框选边界，保持整套视觉一致。",
                accent_body,
            )
        )

        self.motion_choices = PersonalizationChoiceRow(list(MOTION_PRESETS))
        self.chk_reduce_motion = QCheckBox("减少动态效果（关闭位移与连续动画）")
        motion_body = QVBoxLayout()
        motion_body.setContentsMargins(0, 0, 0, 0)
        motion_body.setSpacing(10)
        motion_body.addWidget(self.motion_choices)
        motion_body.addWidget(self.chk_reduce_motion)
        layout.addWidget(
            self._card(
                "动效节奏",
                "所有动画都会停止在明确状态，不使用扫描线或无意义循环。",
                motion_body,
            )
        )

        self.density_choices = PersonalizationChoiceRow(
            list(DENSITY_PRESETS), compact=True
        )
        self.surface_choices = PersonalizationChoiceRow(
            list(SURFACE_PRESETS), compact=True
        )
        layout_body = QVBoxLayout()
        layout_body.setContentsMargins(0, 0, 0, 0)
        layout_body.setSpacing(10)
        layout_body.addWidget(self.density_choices)
        layout_body.addWidget(self.surface_choices)
        layout.addWidget(
            self._card(
                "密度与表面",
                "改变控件呼吸感与层级，不使用廉价玻璃拟态。",
                layout_body,
            )
        )

        self.personalization_preview = AppearancePreview()
        self.personalization_hint = QLabel("实时预览 · 保存后应用到主界面")
        self.personalization_hint.setObjectName("PersonalizationHint")
        self.btn_replay_personalization = QPushButton("重播动效")
        self.btn_replay_personalization.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reset_personalization = QPushButton("恢复默认")
        self.btn_reset_personalization.setObjectName("PersonalizationResetButton")
        self.btn_reset_personalization.setCursor(Qt.CursorShape.PointingHandCursor)
        preview_actions = QHBoxLayout()
        preview_actions.setContentsMargins(0, 0, 0, 0)
        preview_actions.addWidget(self.personalization_hint, 1)
        preview_actions.addWidget(self.btn_replay_personalization)
        preview_actions.addWidget(self.btn_reset_personalization)
        preview_body = QVBoxLayout()
        preview_body.setContentsMargins(0, 0, 0, 0)
        preview_body.setSpacing(10)
        preview_body.addWidget(self.personalization_preview)
        preview_body.addLayout(preview_actions)
        layout.addWidget(
            self._card(
                "实时体验",
                "每次选择都会重演一次“框选—识别—译文出现”，不会常驻循环。",
                preview_body,
            )
        )

        for choices in (
            self.palette_choices,
            self.accent_choices,
            self.motion_choices,
            self.density_choices,
            self.surface_choices,
        ):
            choices.valueChanged.connect(self._refresh_personalization_preview)
        self.chk_reduce_motion.toggled.connect(self._refresh_personalization_preview)
        self.btn_replay_personalization.clicked.connect(self.personalization_preview.replay)
        self.btn_reset_personalization.clicked.connect(self._reset_personalization)
        layout.addStretch(1)
        return tab

    def _build_ocr_tab(self) -> QWidget:
        tab, layout = self._new_page()
        self.combo_engine = QComboBox()
        for engine in list_ocr_engines():
            self.combo_engine.addItem(engine, engine)
        self.combo_lang = QComboBox()
        for code, name in LANGUAGES:
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

        engine_form = self._new_form()
        engine_form.addRow("OCR 引擎", self.combo_engine)
        engine_form.addRow("识别语言", self.combo_lang)
        engine_form.addRow(self.chk_gpu)
        engine_form.addRow(self.chk_orientation)
        layout.addWidget(self._card("识别引擎", "选择文字识别方式与运行能力。", engine_form))

        self.paddle_component_status = QLabel()
        self.paddle_component_status.setWordWrap(True)
        self.paddle_component_progress = QProgressBar()
        self.paddle_component_progress.setTextVisible(False)
        self.paddle_component_progress.setRange(0, 100)
        self.paddle_component_progress.hide()
        self.btn_install_paddle = QPushButton("下载 PaddleOCR 组件")
        self.btn_install_paddle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_install_paddle.clicked.connect(self._install_paddle_component)
        component_actions = QHBoxLayout()
        component_actions.setContentsMargins(0, 0, 0, 0)
        component_actions.addWidget(self.paddle_component_status, 1)
        component_actions.addWidget(self.btn_install_paddle)
        component_body = QVBoxLayout()
        component_body.setContentsMargins(0, 0, 0, 0)
        component_body.setSpacing(10)
        component_body.addLayout(component_actions)
        component_body.addWidget(self.paddle_component_progress)
        layout.addWidget(
            self._card(
                "可选高精度组件",
                "轻量版默认使用 Windows OCR；需要更高识别率时再下载 PaddleOCR 与模型。",
                component_body,
            )
        )
        self._refresh_paddle_component_status()

        merge_form = self._new_form()
        merge_form.addRow("最低置信度", self.spin_confidence)
        merge_form.addRow("行合并容差", self.spin_y_tol)
        merge_form.addRow("横向合并阈值", self.spin_x_gap)
        layout.addWidget(self._card("文本组合", "控制零散文字块如何合并成自然语句。", merge_form))
        layout.addStretch(1)
        return tab

    def _refresh_paddle_component_status(self) -> None:
        if PaddleOCREngine.local_available():
            self.paddle_component_status.setText("当前完整版已内置 PaddleOCR")
            self.btn_install_paddle.setEnabled(False)
            self.btn_install_paddle.setText("已内置")
            return
        manager = component_manager(self.config)
        if manager.is_installed():
            installed = manager.installed_manifest()
            version = installed.version if installed is not None else ""
            self.paddle_component_status.setText(f"PaddleOCR 组件 {version} 已安装")
            self.btn_install_paddle.setEnabled(True)
            self.btn_install_paddle.setText("检查组件更新")
        else:
            self.paddle_component_status.setText("未安装，不影响 Windows OCR 使用")
            self.btn_install_paddle.setEnabled(True)
            self.btn_install_paddle.setText("下载 PaddleOCR 组件")

    def _install_paddle_component(self) -> None:
        if self._paddle_component_task is not None and self._paddle_component_task.isRunning():
            return
        self.btn_install_paddle.setEnabled(False)
        self.btn_install_paddle.setText("检查并下载…")
        self.paddle_component_progress.setRange(0, 100)
        self.paddle_component_progress.setValue(0)
        self.paddle_component_progress.show()
        task = PaddleComponentInstallTask(
            component_manager(self.config), parent=QApplication.instance()
        )
        self._paddle_component_task = task
        task.progress.connect(self._paddle_component_download_progress)
        task.completed.connect(self._paddle_component_installed)
        task.failed.connect(self._paddle_component_failed)
        task.finished.connect(
            lambda current=task: self._paddle_component_task_finished(current)
        )
        task.finished.connect(task.deleteLater)
        task.start()

    def _paddle_component_download_progress(self, completed: int, total: int) -> None:
        if total > 0:
            percent = max(0, min(100, round(completed * 100 / total)))
            self.paddle_component_progress.setRange(0, 100)
            self.paddle_component_progress.setValue(percent)
            self.paddle_component_status.setText(f"正在下载 PaddleOCR 与模型… {percent}%")
        else:
            self.paddle_component_progress.setRange(0, 0)
            self.paddle_component_status.setText("正在下载 PaddleOCR 与模型…")

    def _paddle_component_installed(self, _entrypoint: str) -> None:
        self.paddle_component_progress.setRange(0, 100)
        self.paddle_component_progress.setValue(100)
        self._set_combo(self.combo_engine, "paddle")
        self._refresh_paddle_component_status()
        self.paddle_component_status.setText(
            "PaddleOCR 与模型已是最新版，保存设置后启用"
        )

    def _paddle_component_failed(self, message: str) -> None:
        self.paddle_component_progress.hide()
        self.paddle_component_status.setText(f"组件安装失败：{message}")
        self.btn_install_paddle.setEnabled(True)
        self.btn_install_paddle.setText("重试下载")

    def _paddle_component_task_finished(self, task) -> None:
        if self._paddle_component_task is task:
            self._paddle_component_task = None

    def _build_translation_tab(self) -> QWidget:
        tab, layout = self._new_page()
        form = self._new_form()
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
        layout.addWidget(self._card("翻译路径", "选择语言、服务与失败重试策略。", form))

        key_form = self._new_form()
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
        layout.addWidget(
            self._card(
                "在线服务凭据",
                "优先读取环境变量；此处仅用于当前设备的本地配置。",
                key_form,
            )
        )

        privacy = QLabel(
            "隐私提示：使用在线翻译服务时，截图识别出的文字会发送到第三方服务器。"
            "本应用默认不保存截图、不上传图片，请自行确认服务商隐私政策。"
        )
        privacy.setObjectName("NoticeLabel")
        privacy.setWordWrap(True)
        layout.addWidget(privacy)
        layout.addStretch(1)
        return tab

    def _build_overlay_tab(self) -> QWidget:
        tab, layout = self._new_page()

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

        text_form = self._new_form()
        text_form.addRow("字体", self.combo_font)
        text_form.addRow("译文显示形式", self.combo_display_mode)
        text_form.addRow("字号", self.spin_font_size)
        text_form.addRow("最小字号", self.spin_min_font)
        layout.addWidget(self._card("文字排版", "让译文保持清晰，并匹配原始文字层级。", text_form))

        color_form = self._new_form()
        color_form.addRow("文字颜色", self.btn_text_color)
        color_form.addRow(self.chk_auto_color)
        color_form.addRow("背景颜色", self.btn_bg_color)
        color_form.addRow(self.chk_auto_bg)
        color_form.addRow("背景透明度", self.slider_bg_alpha)
        layout.addWidget(self._card("颜色与背景", "自动模式会按屏幕内容选择对比度。", color_form))

        shape_form = self._new_form()
        shape_form.addRow("内边距", self.spin_padding)
        shape_form.addRow("圆角", self.spin_radius)
        shape_form.addRow(self.chk_show_border)
        layout.addWidget(self._card("边界与留白", "调整译文块的呼吸感与边界提示。", shape_form))
        layout.addStretch(1)
        return tab

    def _build_hotkey_tab(self) -> QWidget:
        tab, layout = self._new_page()
        form = self._new_form()
        self.hotkey_edits: dict[str, QKeySequenceEdit] = {}
        for action, label in _HOTKEY_ACTIONS:
            editor = QKeySequenceEdit()
            editor.setMaximumSequenceLength(1)
            self.hotkey_edits[action] = editor
            form.addRow(label, editor)
        layout.addWidget(self._card("全局快捷键", "即使主窗口隐藏，也能直接启动常用动作。", form))
        layout.addStretch(1)
        return tab

    def _personalization_state(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "palette": self.palette_choices.value(),
            "accent": self.accent_choices.value(),
            "motion_profile": self.motion_choices.value(),
            "density": self.density_choices.value(),
            "surface": self.surface_choices.value(),
            "reduce_motion": self.chk_reduce_motion.isChecked(),
        }

    def _refresh_personalization_preview(
        self, *_args, animate: bool = True
    ) -> None:
        state = self._personalization_state()
        self.palette_choices.set_accent(str(state["accent"]))
        self.personalization_preview.set_options(state, animate=animate)
        dirty = state != getattr(self, "_loaded_appearance", state)
        self.personalization_hint.setProperty("dirty", dirty)
        self.personalization_hint.setText(
            "预览中 · 保存后应用到主界面"
            if dirty
            else "实时预览 · 当前设置已保存"
        )
        self.personalization_hint.style().unpolish(self.personalization_hint)
        self.personalization_hint.style().polish(self.personalization_hint)

    def _reset_personalization(self) -> None:
        state = copy.deepcopy(DEFAULTS["appearance"])
        self.palette_choices.set_value(str(state["palette"]), emit=False)
        self.accent_choices.set_value(str(state["accent"]), emit=False)
        self.motion_choices.set_value(str(state["motion_profile"]), emit=False)
        self.density_choices.set_value(str(state["density"]), emit=False)
        self.surface_choices.set_value(str(state["surface"]), emit=False)
        self.chk_reduce_motion.blockSignals(True)
        self.chk_reduce_motion.setChecked(bool(state["reduce_motion"]))
        self.chk_reduce_motion.blockSignals(False)
        self._refresh_personalization_preview()

    # ------------------------------------------------------------- load/save
    def _load(self) -> None:
        cfg = self.config
        self._set_combo(self.combo_display_mode, bool(cfg.get("translation.keep_original", False)))
        self.chk_autostart.setChecked(bool(cfg.get("general.startup_with_system", False)))
        self.chk_tray.setChecked(bool(cfg.get("general.minimize_to_tray", True)))
        self.chk_history.setChecked(bool(cfg.get("general.save_history", False)))
        self.history_dir.setText(str(cfg.get("general.history_dir", "")))
        self.chk_auto_updates.setChecked(bool(cfg.get("updates.auto_check", True)))

        appearance = copy.deepcopy(DEFAULTS["appearance"])
        appearance.update(cfg.section("appearance"))
        self.palette_choices.set_value(str(appearance["palette"]), emit=False, animate=False)
        self.accent_choices.set_value(str(appearance["accent"]), emit=False, animate=False)
        self.motion_choices.set_value(
            str(appearance["motion_profile"]), emit=False, animate=False
        )
        self.density_choices.set_value(
            str(appearance["density"]), emit=False, animate=False
        )
        self.surface_choices.set_value(
            str(appearance["surface"]), emit=False, animate=False
        )
        self.chk_reduce_motion.setChecked(bool(appearance["reduce_motion"]))
        self._loaded_appearance = self._personalization_state()
        self._refresh_personalization_preview(animate=False)

        self._set_combo(self.combo_engine, cfg.get("ocr.engine", "windows"))
        self._set_combo(self.combo_lang, cfg.get("ocr.lang", "auto"))
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
            self.hotkey_edits[action].setKeySequence(
                self._hotkey_sequence(str(cfg.get(f"hotkeys.{action}", "")))
            )

        self.btn_text_color.clicked.connect(lambda: self._pick_color("text"))
        self.btn_bg_color.clicked.connect(lambda: self._pick_color("bg"))

    def _on_accept(self) -> None:
        """A short, deterministic save acknowledgement; no page compositing involved."""
        hotkeys = self._validated_hotkeys()
        if hotkeys is None:
            return
        self._save_close_timer.stop()
        self.btn_save.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        self.btn_save.setProperty("saved", False)
        self.btn_save.setText("保存中…")
        self.btn_save.style().unpolish(self.btn_save)
        self.btn_save.style().polish(self.btn_save)
        self._pending_hotkeys = hotkeys
        self._commit_timer.start(80)

    def _commit_pending(self) -> None:
        hotkeys = self._pending_hotkeys
        self._pending_hotkeys = None
        if hotkeys is not None:
            self._commit_accept(hotkeys)

    def _validated_hotkeys(self) -> dict[str, str] | None:
        labels = dict(_HOTKEY_ACTIONS)
        hotkeys: dict[str, str] = {}
        normalized: dict[str, str] = {}
        try:
            for action, _ in _HOTKEY_ACTIONS:
                value = self.hotkey_edits[action].keySequence().toString(
                    QKeySequence.SequenceFormat.PortableText
                )
                hotkeys[action] = value
                normalized[action] = normalize_hotkey(value)
        except (HotkeyError, ValueError) as exc:
            QMessageBox.warning(self, "快捷键无效", str(exc))
            return None

        seen: dict[str, str] = {}
        for action, combo in normalized.items():
            if not combo:
                continue
            if combo in seen:
                first = labels.get(seen[combo], seen[combo])
                second = labels.get(action, action)
                QMessageBox.warning(
                    self,
                    "快捷键冲突",
                    f"{combo} 被重复绑定：{first} / {second}",
                )
                return None
            seen[combo] = action
        return hotkeys

    def _commit_accept(self, hotkeys: dict[str, str]) -> None:
        cfg = self.config
        original_data = cfg.data
        cfg.data = copy.deepcopy(original_data)
        try:
            cfg.set("general.startup_with_system", self.chk_autostart.isChecked())
            cfg.set("general.minimize_to_tray", self.chk_tray.isChecked())
            cfg.set("general.save_history", self.chk_history.isChecked())
            cfg.set("general.history_dir", self.history_dir.text().strip())
            cfg.set("updates.auto_check", self.chk_auto_updates.isChecked())

            appearance = self._personalization_state()
            for key, value in appearance.items():
                cfg.set(f"appearance.{key}", value)

            cfg.set("ocr.engine", self.combo_engine.currentData() or "windows")
            cfg.set("ocr.lang", self.combo_lang.currentData() or "auto")
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

            for action, combo in hotkeys.items():
                cfg.set(f"hotkeys.{action}", combo)
            cfg.save()
        except (OSError, ValueError, TypeError) as exc:
            cfg.data = original_data
            self.btn_save.setEnabled(True)
            self.btn_cancel.setEnabled(True)
            self.btn_save.setText("保存设置")
            QMessageBox.warning(self, "保存失败", f"配置没有被修改。\n{exc}")
            return

        self._loaded_appearance = self._personalization_state()
        self._refresh_personalization_preview(animate=False)

        self.btn_save.setProperty("saved", True)
        self.btn_save.setText("✓ 已保存")
        self.btn_save.style().unpolish(self.btn_save)
        self.btn_save.style().polish(self.btn_save)
        self._close_intent = 1
        self._settings_shell.setEnabled(False)
        self._save_animation.stop()
        base_width = max(116, self.btn_save.width())
        duration = motion_duration(SLOW)
        if duration == 0:
            self.btn_save.setMinimumWidth(116)
        else:
            self._save_animation.setDuration(duration)
            self._save_animation.setKeyValues(
                [(0.0, base_width), (0.42, base_width + 14), (1.0, 116)]
            )
            self._save_animation.start()
        hold = motion_duration(SLOW)
        if hold == 0:
            self.accept()
        else:
            self._save_close_timer.start(hold + 80)

    # ------------------------------------------------------------- helpers
    def _set_combo(self, combo: QComboBox, value: str) -> None:
        idx = combo.findData(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    @staticmethod
    def _hotkey_sequence(value: str) -> QKeySequence:
        """Load both Qt portable shortcuts and older pynput-style values."""
        portable = value.strip()
        if "<" in portable:
            aliases = {
                "<ctrl>": "Ctrl",
                "<shift>": "Shift",
                "<alt>": "Alt",
                "<cmd>": "Meta",
                "<esc>": "Esc",
                "<space>": "Space",
            }
            lowered = portable.lower()
            for source, target in aliases.items():
                lowered = lowered.replace(source, target)
            portable = lowered.replace("<", "").replace(">", "")
        return QKeySequence.fromString(portable, QKeySequence.SequenceFormat.PortableText)

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
        for button, color in (
            (self.btn_text_color, self._text_color),
            (self.btn_bg_color, self._bg_color),
        ):
            value = color.name().upper()
            foreground = "#292D2A" if color.lightnessF() > 0.58 else "#FFFFFF"
            button.setText(f"●  {value}")
            button.setStyleSheet(
                f"color: {foreground}; background: {value}; border: 1px solid {value};"
                "border-radius: 8px; padding: 6px 10px;"
            )

    def _sync_display_mode(self, *_args) -> None:
        """覆盖层"译文显示形式"与翻译页"保留原文"保持同步。"""
        self.chk_keep_original.blockSignals(True)
        self.chk_keep_original.setChecked(bool(self.combo_display_mode.currentData()))
        self.chk_keep_original.blockSignals(False)

    def _sync_keep_original(self, checked: bool) -> None:
        self.combo_display_mode.blockSignals(True)
        self._set_combo(self.combo_display_mode, bool(checked))
        self.combo_display_mode.blockSignals(False)
