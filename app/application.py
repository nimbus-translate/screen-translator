"""应用控制器：把截图、OCR、翻译、覆盖层、托盘、快捷键串起来。"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QMessageBox, QWidget

from app.config import AppConfig
from app.hotkeys import HotkeyError, HotkeyManager
from app.logger import app_data_dir, get_logger
from app.models import CaptureInfo, TextRegion
from app.version import __version__

# 确保各 OCR 引擎 / 翻译适配器已注册（顺序影响 UI 下拉框默认项）
import services.ocr.paddle_ocr  # noqa: F401
import services.ocr.windows_ocr  # noqa: F401
import services.ocr.null_ocr  # noqa: F401
import services.translation.mock_translator  # noqa: F401
import services.translation.mymemory_translator  # noqa: F401
import services.translation.google_free_translator  # noqa: F401
import services.translation.openai_translator  # noqa: F401
import services.translation.deepl_translator  # noqa: F401
import services.translation.google_translator  # noqa: F401

from services.ocr.base import OCRUnavailableError, create_ocr_engine, list_ocr_engines
from services.authenticode import (
    AuthenticodeVerificationError,
    runtime_signature_reference,
    verify_authenticode,
)
from services.screenshot_service import ScreenshotService
from services.update_service import sha256_file
from services.translation.base import Translator
from services.translation.cache import TranslationCache
from services.translation.factory import create_translator, list_translators
from services.window_capture_service import (
    WindowCaptureError,
    get_foreground_window,
    get_window_rect_physical,
    get_window_title,
    is_current_process_window,
    is_window_capturable,
    window_capture_available,
)
from ui.appearance import resolve_tokens
from ui.main_window import MainWindow
from ui.floating_status import FloatingStatus
from ui.motion import CAPTURE_SETTLE, SELECTION_SETTLE, STATUS_HOLD
from ui.overlay_manager import OverlayManager
from ui.selection_overlay import SelectionOverlay
from ui.settings_dialog import SettingsDialog
from ui.style import apply_style
from ui.tray_icon import TrayIcon, build_icon
from ui.ocr_component_tasks import PaddleComponentInstallTask
from ui.update_tasks import UpdateCheckTask, UpdateDownloadTask
from ui.window_capture_highlight import WindowCaptureHighlight
from utils import dpi_utils
from utils.language_utils import LANGUAGES, LANGUAGE_CODES
from workers.translation_worker import PipelineTask

log = get_logger("application")


@dataclass
class _CaptureSession:
    mode: str
    window_was_visible: bool
    window_was_minimized: bool
    overlay_was_visible: bool
    target_hwnd: int = 0


class Application(QObject):
    """顶层控制器。"""

    def __init__(self, qt_app: QGuiApplication) -> None:
        super().__init__()
        self.qt_app = qt_app
        self.config = AppConfig()
        self.monitor_map: list[dpi_utils.MonitorInfo] = []

        self.screenshot_service = ScreenshotService()
        self.overlay_manager: OverlayManager | None = None
        self.floating_status = FloatingStatus()
        self.hotkey_manager = HotkeyManager(self)
        self.translator: Translator | None = None
        self.ocr_engine = None
        self._active_ocr_engine_name = "none"
        self.cache: TranslationCache | None = None

        self.window: MainWindow | None = None
        self._settings_page: SettingsDialog | None = None
        self._reopen_settings_after_close = False
        self.tray: TrayIcon | None = None
        self.selection: SelectionOverlay | None = None
        self.window_highlight: WindowCaptureHighlight | None = None
        self.worker: PipelineTask | None = None

        self._last_capture: CaptureInfo | None = None
        self._last_window_hwnd = 0
        self._overlay_visible = True
        self._busy = False
        self._last_external_hwnd = 0
        self._capture_session: _CaptureSession | None = None
        self._pending_capture_mode = ""
        self._pending_capture_bbox: tuple[int, int, int, int] | None = None
        self._pending_window_hwnd = 0
        self._highlight_window_rect: tuple[int, int, int, int] | None = None
        self._window_highlight_retries = 0
        self._pipeline_succeeded = False
        self._pipeline_error = ""
        self._shutting_down = False
        self._shutdown_finalized = False
        self._shutdown_aux_tasks: list[QObject] = []
        self._update_check_task: UpdateCheckTask | None = None

        self._capture_timer = QTimer(self)
        self._capture_timer.setSingleShot(True)
        self._capture_timer.timeout.connect(self._execute_pending_capture)
        self._selection_timer = QTimer(self)
        self._selection_timer.setSingleShot(True)
        self._selection_timer.timeout.connect(self._show_selection)
        self._foreground_tracker = QTimer(self)
        self._foreground_tracker.setInterval(200)
        self._foreground_tracker.timeout.connect(self._remember_external_foreground)

        try:
            self.qt_app.styleHints().colorSchemeChanged.connect(
                self._on_system_color_scheme_changed
            )
        except (AttributeError, RuntimeError):
            pass

        self.hotkey_manager.triggered.connect(self.on_hotkey)

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        try:
            self._refresh_appearance()
            self._build_monitor_map()
            self.cache = TranslationCache(
                path=Path(self.config.path.parent) / "translation_cache.json",
                ttl_days=self.config.get("translation.cache_ttl_days", 30),
                max_entries=self.config.get("translation.cache_max_entries", 2000),
            )
            self.ocr_engine = self._create_configured_ocr_engine()
            resolved_service = self._resolve_translator_service()
            if resolved_service != self.config.get("translation.service", "mock"):
                self.config.set("translation.service", resolved_service)
                self.config.save()
                self.set_status(f"检测到 API Key，已自动切换到 {resolved_service} 翻译服务")
            self.translator = create_translator(
                self.config.get("translation.service", "mock"),
                self.config.section("translation"),
                cache=self.cache,
                api_key_resolver=self.config.api_key,
            )
            self.overlay_manager = OverlayManager(self.config)
            self.overlay_manager.set_monitor_map(self.monitor_map)

            self.window = MainWindow(self)
            self.tray = TrayIcon(self)
            capture_available = window_capture_available()
            capture_reason = "当前平台暂不支持原生窗口捕获"
            self.window.set_window_capture_available(capture_available, capture_reason)
            self.tray.set_window_capture_available(capture_available, capture_reason)
            self.window.show()
            self.tray.show()
            self._remember_external_foreground()
            self._foreground_tracker.start()
            self._apply_hotkeys()
            self._apply_autostart()
            self._warmup()
            if self.config.get("updates.auto_check", True):
                QTimer.singleShot(6000, self._check_for_updates)
        except Exception as exc:
            log.exception("应用启动失败")
            QMessageBox.critical(None, "启动失败", f"{exc}")

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self._foreground_tracker.stop()
        self._capture_timer.stop()
        self._selection_timer.stop()
        if self._settings_page is not None:
            self._settings_page.cancel_background_tasks()
        self._cancel_auxiliary_tasks_for_shutdown()
        self._dispose_selection()
        self._dispose_window_highlight()
        self.floating_status.hide_immediate()
        self.hotkey_manager.stop()
        if self.overlay_manager is not None:
            self.overlay_manager.hide_all()
        if self.cache is not None:
            self.cache.flush()
        if self.window is not None:
            self.window.hide()
        if self.tray is not None:
            self.tray.hide()
        if self.worker is not None and self.worker.isRunning():
            self.worker.finished.connect(
                self._maybe_finalize_shutdown, Qt.ConnectionType.UniqueConnection
            )
            self.worker.cancel()
            self.set_status("正在安全结束当前任务…")
        self._maybe_finalize_shutdown()

    def _cancel_auxiliary_tasks_for_shutdown(self) -> None:
        """Cancel Qt background jobs and keep the event loop alive for teardown."""
        task_types = (UpdateCheckTask, UpdateDownloadTask, PaddleComponentInstallTask)
        tasks: list[QObject] = []
        for task_type in task_types:
            tasks.extend(self.qt_app.findChildren(task_type))
        if self._update_check_task is not None and all(
            task is not self._update_check_task for task in tasks
        ):
            tasks.append(self._update_check_task)

        self._shutdown_aux_tasks = []
        for task in tasks:
            try:
                if not task.isRunning():
                    continue
                self._shutdown_aux_tasks.append(task)
                task.finished.connect(
                    lambda current=task: self._auxiliary_shutdown_task_finished(current)
                )
                task.cancel()
                if not task.isRunning():
                    self._auxiliary_shutdown_task_finished(task)
            except RuntimeError:
                # The QObject may already have completed and entered deferred
                # deletion between discovery and cancellation.
                continue
        if self._shutdown_aux_tasks:
            self.set_status("正在安全结束下载与更新任务…")

    def _auxiliary_shutdown_task_finished(self, task: QObject) -> None:
        self._shutdown_aux_tasks = [
            current for current in self._shutdown_aux_tasks if current is not task
        ]
        QTimer.singleShot(0, self._maybe_finalize_shutdown)

    def _maybe_finalize_shutdown(self) -> None:
        if not self._shutting_down or self._shutdown_finalized:
            return
        if self._shutdown_aux_tasks:
            return
        if self.worker is not None and self.worker.isRunning():
            return
        self._finalize_shutdown()

    def _finalize_shutdown(self) -> None:
        if self._shutdown_finalized:
            return
        self._shutdown_finalized = True
        worker = self.worker
        self.worker = None
        if worker is not None:
            worker.deleteLater()
        self.qt_app.quit()

    def _warmup(self) -> None:
        """后台线程预热 OCR 模型，避免第一次截图时卡很久。"""
        if os.environ.get("SCREEN_TRANSLATOR_NO_WARMUP") == "1" or os.environ.get(
            "SCREEN_TRANSLATOR_SELFTEST"
        ) == "1":
            return
        # The optional Paddle worker is a separate process. Starting it from an
        # untracked daemon thread could leave that process behind during a quick
        # app shutdown; its first real OCR request performs the warmup instead.
        if bool(getattr(self.ocr_engine, "uses_external_component", False)):
            return

        def job() -> None:
            try:
                if self.ocr_engine is not None:
                    self.ocr_engine.warmup()
            except Exception as exc:
                log.warning("OCR 模型预热失败：%s", exc)

        import threading

        threading.Thread(target=job, daemon=True, name="ocr-warmup").start()

    def _remember_external_foreground(self) -> None:
        """保留最近一次非本程序前台窗口，供点击“当前窗口”后稳定捕获。"""
        try:
            hwnd = get_foreground_window()
            if hwnd and not is_current_process_window(hwnd) and is_window_capturable(hwnd):
                self._last_external_hwnd = hwnd
        except Exception as exc:
            log.debug("读取前台窗口失败：%s", exc)

    def _resolve_window_target(self) -> int:
        """Freeze the best live external window before the capture transition."""
        candidates = (get_foreground_window(), self._last_external_hwnd)
        for hwnd in candidates:
            try:
                if hwnd and not is_current_process_window(hwnd) and is_window_capturable(hwnd):
                    self._last_external_hwnd = hwnd
                    return hwnd
            except Exception:
                continue
        return 0

    # ------------------------------------------------------------------ dpi/monitor
    def _build_monitor_map(self) -> None:
        self.monitor_map = dpi_utils.build_monitor_map(
            self.qt_app.screens(), dpi_utils.enum_display_monitors_physical()
        )

    def refresh_monitor_map(self) -> None:
        self._build_monitor_map()
        if self.overlay_manager is not None:
            self.overlay_manager.set_monitor_map(self.monitor_map)

    # ------------------------------------------------------------------ actions
    def on_hotkey(self, action: str) -> None:
        log.debug("快捷键触发：%s", action)
        if action == "capture_region":
            self.start_region_capture()
        elif action == "capture_fullscreen":
            self.start_capture("fullscreen")
        elif action == "capture_window":
            self.start_capture("window")
        elif action == "toggle_overlay":
            self.toggle_overlay()
        elif action == "refresh":
            self.refresh()

    def start_region_capture(self) -> None:
        if self._busy or self.window is None or self.overlay_manager is None:
            return
        self._begin_capture_session("region", self._after_region_departure)

    def _after_region_departure(self) -> None:
        self._selection_timer.stop()
        self._selection_timer.start(CAPTURE_SETTLE)

    def _show_selection(self) -> None:
        if self._capture_session is None or self._capture_session.mode != "region":
            return
        try:
            screen = self.qt_app.primaryScreen()
            if screen is None:
                raise RuntimeError("没有检测到可用显示器")
            self._dispose_selection()
            self.selection = SelectionOverlay(
                screen.virtualGeometry(),
                mask_opacity=int(self.config.get("capture.select_mask_opacity", 84)),
                border_color=self._capture_accent(),
            )
            self.selection.selection_done.connect(self._on_selection_done)
            self.selection.cancelled.connect(self._on_selection_cancelled)
            self.selection.show()
            self.selection.raise_()
            self.selection.activateWindow()
            self.selection.setFocus()
        except Exception as exc:
            log.exception("显示框选层失败")
            self._abort_capture(
                "无法开始框选",
                failed=True,
                error_title="无法开始框选",
                error_message=str(exc),
            )

    def _on_selection_done(self, logical_bbox: object) -> None:
        self._dispose_selection()
        if not isinstance(logical_bbox, tuple) or len(logical_bbox) != 4:
            self._abort_capture(
                "框选坐标无效",
                failed=True,
                error_title="框选失败",
                error_message="收到的框选坐标格式无效，请重试。",
            )
            return
        parts = dpi_utils.logical_rect_to_physical_parts(logical_bbox, self.monitor_map)
        if not parts:
            self._abort_capture("框选区域太小，已取消")
            return
        if not dpi_utils.parts_form_rectangle(parts):
            self._abort_capture(
                "框选跨越了不同缩放比例的屏幕",
                failed=True,
                error_title="请在单个屏幕内框选",
                error_message="跨不同缩放比例的显示器会产生非矩形像素区域，请在一个屏幕内完成框选。",
            )
            return
        physical = dpi_utils.union_rects(parts)
        if physical[2] - physical[0] < 4 or physical[3] - physical[1] < 4:
            self._abort_capture("框选区域太小，已取消")
            return
        self._schedule_capture("region", bbox=physical, delay_ms=SELECTION_SETTLE)

    def _on_selection_cancelled(self) -> None:
        self._dispose_selection()
        self._abort_capture("已取消框选")

    def start_capture(self, mode: str) -> None:
        if self._busy or self.window is None or self.overlay_manager is None:
            return
        if mode not in {"fullscreen", "window"}:
            return
        if mode == "window":
            if not window_capture_available():
                self.window.play_capture_failure("window")
                self.set_status("当前平台暂不支持原生窗口捕获")
                return
            hwnd = self._resolve_window_target()
            if not hwnd:
                self.window.play_capture_failure("window")
                self.set_status("请先切换到要翻译的窗口，再使用当前窗口翻译")
                return
            self._begin_capture_session(
                "window",
                self._after_window_departure,
                target_hwnd=hwnd,
            )
            return
        self._begin_capture_session("fullscreen", self._after_fullscreen_departure)

    def _after_fullscreen_departure(self) -> None:
        self._schedule_capture("fullscreen", delay_ms=CAPTURE_SETTLE)

    def _after_window_departure(self) -> None:
        session = self._capture_session
        if session is None or session.mode != "window":
            return
        try:
            rect = get_window_rect_physical(session.target_hwnd)
            intersecting = [
                monitor
                for monitor in self.monitor_map
                if dpi_utils.intersect(rect, monitor.physical) is not None
            ]
            # A single logical outline cannot faithfully cover a window split
            # across different per-monitor coordinate spaces. Skip the outline,
            # keep the target frozen, and proceed after the compositor settles.
            if len(intersecting) != 1:
                self.set_status("已锁定跨屏窗口，正在准备捕获…")
                self._schedule_capture(
                    "window",
                    target_hwnd=session.target_hwnd,
                    delay_ms=CAPTURE_SETTLE,
                )
                return
            center_x = (rect[0] + rect[2]) // 2
            center_y = (rect[1] + rect[3]) // 2
            monitor = intersecting[0]
            geo = dpi_utils.physical_rect_to_overlay_geometry(rect, monitor)
            self._highlight_window_rect = rect
            self._dispose_window_highlight()
            self.window_highlight = WindowCaptureHighlight(
                QRect(geo[0], geo[1], max(1, geo[2]), max(1, geo[3])),
                get_window_title(session.target_hwnd),
                accent_color=self._capture_accent(),
            )
            self.window_highlight.finished.connect(self._on_window_highlight_finished)
            self.window_highlight.show_and_confirm()
        except Exception as exc:
            log.exception("确认目标窗口失败")
            self._abort_capture(
                "目标窗口已经不可用",
                failed=True,
                error_title="窗口捕获失败",
                error_message=str(exc),
            )

    def _on_window_highlight_finished(self) -> None:
        session = self._capture_session
        hwnd = session.target_hwnd if session is not None else 0
        self._dispose_window_highlight()
        if not hwnd:
            self._abort_capture("目标窗口已经不可用", failed=True)
            return
        try:
            current_rect = get_window_rect_physical(hwnd)
        except WindowCaptureError as exc:
            self._abort_capture(
                "目标窗口已经不可用",
                failed=True,
                error_title="窗口捕获失败",
                error_message=str(exc),
            )
            return
        if (
            self._highlight_window_rect is not None
            and current_rect != self._highlight_window_rect
            and self._window_highlight_retries < 1
        ):
            self._window_highlight_retries += 1
            self._after_window_departure()
            return
        self._schedule_capture(
            "window",
            target_hwnd=hwnd,
            delay_ms=SELECTION_SETTLE,
        )

    def _begin_capture_session(
        self,
        mode: str,
        continuation,
        *,
        target_hwnd: int = 0,
    ) -> bool:
        if self._busy or self.window is None or self.overlay_manager is None:
            return False
        self.window.settle_settings_transition_for_capture()
        try:
            self.refresh_monitor_map()
        except Exception as exc:
            log.exception("刷新显示器映射失败")
            self.show_error("无法开始截图", str(exc))
            return False
        self._capture_session = _CaptureSession(
            mode=mode,
            window_was_visible=self.window.isVisible(),
            window_was_minimized=self.window.isMinimized(),
            overlay_was_visible=self.overlay_manager.is_visible(),
            target_hwnd=target_hwnd,
        )
        self._pipeline_succeeded = False
        self._pipeline_error = ""
        self._highlight_window_rect = None
        self._window_highlight_retries = 0
        self._set_busy(True)
        self.floating_status.hide_immediate()
        self.overlay_manager.hide_all()
        self._sync_overlay_state(False)
        self.window.play_capture_departure(mode, continuation)
        return True

    def _schedule_capture(
        self,
        mode: str,
        *,
        bbox: tuple[int, int, int, int] | None = None,
        target_hwnd: int = 0,
        delay_ms: int = CAPTURE_SETTLE,
    ) -> None:
        if self._capture_session is None:
            return
        self._capture_timer.stop()
        self._pending_capture_mode = mode
        self._pending_capture_bbox = bbox
        self._pending_window_hwnd = target_hwnd
        if delay_ms <= 0:
            self._execute_pending_capture()
        else:
            self._capture_timer.start(delay_ms)

    def _execute_pending_capture(self) -> None:
        mode = self._pending_capture_mode
        bbox = self._pending_capture_bbox
        hwnd = self._pending_window_hwnd
        self._pending_capture_mode = ""
        self._pending_capture_bbox = None
        self._pending_window_hwnd = 0
        if self._capture_session is None:
            return
        # There must be no top-most UI alive when the actual pixels are read.
        self.floating_status.hide_immediate()
        try:
            if mode == "fullscreen":
                capture = self.screenshot_service.capture_fullscreen()
            elif mode == "window":
                if not hwnd:
                    raise WindowCaptureError("没有可用的目标窗口")
                capture = self.screenshot_service.capture_window(hwnd)
                self._last_window_hwnd = hwnd
            elif mode == "region" and bbox is not None:
                capture = self.screenshot_service.capture_bbox(bbox)
                capture.mode = "region"
            else:
                raise RuntimeError("截图请求缺少有效范围")
        except Exception as exc:
            log.exception("截图失败")
            self._abort_capture(
                "截图失败",
                failed=True,
                error_title="截图失败",
                error_message=str(exc),
            )
            return
        self._accept_capture(capture)

    def run_capture_rect(
        self, bbox: tuple[int, int, int, int], mode: str, existing: CaptureInfo | None = None
    ) -> None:
        if self._busy:
            return
        if self.window is None or self.overlay_manager is None:
            return
        def continue_capture() -> None:
            if existing is not None:
                existing.mode = mode
                self._accept_capture(existing)
            else:
                self._schedule_capture(mode, bbox=bbox, delay_ms=CAPTURE_SETTLE)

        self._begin_capture_session(mode, continue_capture)

    def _accept_capture(self, capture: CaptureInfo) -> None:
        if self._capture_session is None:
            return
        self._last_capture = capture
        self.floating_status.show_fade("正在识别…", anchor=self._capture_anchor(capture.bbox))
        self._start_pipeline(capture)

    def _start_pipeline(self, capture: CaptureInfo) -> None:
        if not self._cancel_worker():
            self._abort_capture(
                "上一次翻译仍在结束，请稍后重试",
                failed=True,
                error_title="任务仍在运行",
                error_message="旧任务尚未安全结束，没有启动新的翻译。",
            )
            return
        try:
            config_snapshot = copy.copy(self.config)
            config_snapshot.data = copy.deepcopy(self.config.data)
            self.worker = PipelineTask(
                capture=capture,
                ocr_engine=self.ocr_engine,
                translator=self.translator,
                config=config_snapshot,
            )
            self.worker.status.connect(self._on_worker_status)
            self.worker.error.connect(self._on_worker_error)
            self.worker.result.connect(self.on_pipeline_result)
            self.worker.finished.connect(self._on_worker_finished)
            self.worker.start()
        except Exception as exc:
            log.exception("启动翻译任务失败")
            self._abort_capture(
                "无法启动翻译任务",
                failed=True,
                error_title="处理失败",
                error_message=str(exc),
            )

    def _on_worker_status(self, text: str) -> None:
        signal_sender = self.sender()
        if (
            self._shutting_down
            or self._capture_session is None
            or (signal_sender is not None and signal_sender is not self.worker)
        ):
            return
        self.set_status(text)
        self.floating_status.set_text(text)

    def _on_worker_error(self, message: str) -> None:
        signal_sender = self.sender()
        if (
            self._shutting_down
            or self._capture_session is None
            or (signal_sender is not None and signal_sender is not self.worker)
        ):
            return
        self._pipeline_error = message
        self.set_status(f"处理失败：{message}")

    def _on_worker_finished(self) -> None:
        signal_sender = self.sender()
        if signal_sender is not None and signal_sender is not self.worker:
            return
        finished_worker = self.worker
        if finished_worker is None:
            return
        self.worker = None
        finished_worker.deleteLater()
        if self._shutting_down:
            return
        if self._pipeline_succeeded:
            session = self._capture_session
            self._capture_session = None
            if self.window is not None and session is not None:
                self.window.finish_capture(session.mode)
            self._set_busy(False)
            self.floating_status.hide_fade(delay_ms=STATUS_HOLD)
            return
        message = self._pipeline_error or "处理已取消"
        show_dialog = bool(self._pipeline_error)
        self._abort_capture(
            message,
            failed=show_dialog,
            error_title="处理失败" if show_dialog else "",
            error_message=message if show_dialog else "",
        )

    def _cancel_worker(self) -> bool:
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            if not self.worker.wait(1000):
                return False
        if self.worker is not None:
            self.worker.deleteLater()
        self.worker = None
        return True

    def _capture_anchor(self, bbox: tuple[int, int, int, int]) -> QPoint | None:
        if not self.monitor_map:
            return None
        px = (bbox[0] + bbox[2]) // 2
        py = (bbox[1] + bbox[3]) // 2
        monitor = dpi_utils.monitor_for_physical_point(px, py, self.monitor_map)
        local_x, local_y = dpi_utils.physical_to_local_logical(px, py, monitor)
        return QPoint(
            monitor.logical_origin[0] + local_x,
            monitor.logical_origin[1] + local_y,
        )

    def _dispose_selection(self) -> None:
        selection = self.selection
        self.selection = None
        if selection is not None:
            selection.dismiss()
            selection.deleteLater()

    def _dispose_window_highlight(self) -> None:
        highlight = self.window_highlight
        self.window_highlight = None
        if highlight is not None:
            highlight.dismiss()
            highlight.deleteLater()

    def _abort_capture(
        self,
        status: str,
        *,
        failed: bool = False,
        error_title: str = "",
        error_message: str = "",
    ) -> None:
        self._capture_timer.stop()
        self._selection_timer.stop()
        self._pending_capture_mode = ""
        self._pending_capture_bbox = None
        self._pending_window_hwnd = 0
        self._dispose_selection()
        self._dispose_window_highlight()
        self.floating_status.hide_immediate()

        session = self._capture_session
        self._capture_session = None
        if self.window is not None and session is not None:
            if session.window_was_visible:
                self.window.restore_after_capture(
                    session.mode,
                    was_minimized=session.window_was_minimized,
                    failed=failed,
                )
            elif failed:
                self.window.play_capture_failure(session.mode)
        self.set_status(status)
        if error_title and error_message:
            self.show_error(error_title, error_message)
        if self.overlay_manager is not None:
            if session is not None and session.overlay_was_visible:
                self.overlay_manager.show_all()
                self._sync_overlay_state(True)
            else:
                self.overlay_manager.hide_all()
                self._sync_overlay_state(False)
        self._set_busy(False)

    def on_pipeline_result(self, payload: dict) -> None:
        signal_sender = self.sender()
        if (
            self._shutting_down
            or self._capture_session is None
            or (signal_sender is not None and signal_sender is not self.worker)
        ):
            return
        capture: CaptureInfo = payload["capture"]
        regions: list[TextRegion] = payload["regions"]
        recognized_count = int(payload.get("recognized_count", len(regions)))
        translated_count = int(
            payload.get("translated_count", max(0, len(regions)))
        )
        failed_count = int(payload.get("failed_count", 0))
        if self.overlay_manager is None:
            return
        if not regions:
            self._pipeline_error = "没有识别到可翻译的文字"
            return
        # Preserve the previous translation until a new result actually exists;
        # that lets cancellation/OCR errors restore it without stale ghost windows.
        self.overlay_manager.clear_all()
        overlay_visible = self.overlay_manager.show_regions(capture, regions)
        self._sync_overlay_state(overlay_visible)
        self._pipeline_succeeded = True
        if failed_count:
            self.set_status(
                f"识别 {recognized_count} 个文本块，已翻译 {translated_count} 个，"
                f"{failed_count} 个保留原文"
            )
        else:
            self.set_status(
                f"翻译完成：识别 {recognized_count} 个文本块，"
                f"已翻译 {translated_count} 个"
            )
        self.floating_status.set_text("翻译完成")
        self._save_history(capture, regions)

    # ------------------------------------------------------------------ overlay
    def toggle_overlay(self) -> None:
        if self.overlay_manager is None or self._busy:
            return
        visible = not self._overlay_visible
        if visible:
            self.overlay_manager.show_all()
        else:
            self.overlay_manager.hide_all(animate=True)
        self._sync_overlay_state(visible)

    def _sync_overlay_state(self, visible: bool) -> None:
        self._overlay_visible = bool(visible)
        if self.tray is not None:
            self.tray.set_overlay_checked(self._overlay_visible)
        if self.window is not None:
            self.window.set_overlay_checked(self._overlay_visible)

    def refresh(self) -> None:
        if self._last_capture is None:
            self.set_status("还没有截图，先截一张再说")
            return
        if self._busy:
            return
        mode = self._last_capture.mode
        if mode == "window":
            hwnd = self._last_window_hwnd
            if not hwnd or not is_window_capturable(hwnd):
                if self.window is not None:
                    self.window.play_capture_failure("window")
                self.set_status("上次翻译的窗口已经关闭或最小化")
                return
            self._begin_capture_session(
                "window",
                lambda: self._schedule_capture(
                    "window", target_hwnd=hwnd, delay_ms=CAPTURE_SETTLE
                ),
                target_hwnd=hwnd,
            )
            return
        if mode == "fullscreen":
            self._begin_capture_session(
                "fullscreen",
                lambda: self._schedule_capture("fullscreen", delay_ms=CAPTURE_SETTLE),
            )
            return
        bbox = self._last_capture.bbox
        self._begin_capture_session(
            "region",
            lambda: self._schedule_capture(
                "region", bbox=bbox, delay_ms=CAPTURE_SETTLE
            ),
        )

    def set_edit_mode(self, enabled: bool) -> None:
        if self.overlay_manager is None or self._busy:
            return
        self.overlay_manager.set_edit_mode(enabled)
        if self.window is not None:
            self.window.set_edit_mode_checked(enabled)

    # ------------------------------------------------------------------ settings
    def _capture_accent(self) -> str:
        """Resolve the capture accent from the versioned appearance settings."""
        return resolve_tokens(self.config.section("appearance")).accent

    def _refresh_appearance(self) -> None:
        tokens = apply_style(self.qt_app, self.config.section("appearance"))
        self.qt_app.setWindowIcon(build_icon(tokens))
        self.floating_status.refresh_appearance()
        if self.selection is not None:
            self.selection.set_accent(tokens.accent)
        if self.window_highlight is not None:
            self.window_highlight.set_accent(tokens.accent)
        if self.tray is not None:
            self.tray.refresh_appearance()
        if self.window is not None:
            self.window.refresh_appearance()
        if self._settings_page is not None:
            self._settings_page.refresh_appearance()

    def _on_system_color_scheme_changed(self, _scheme) -> None:
        if self.config.get("appearance.palette", "warm_paper") == "system":
            self._refresh_appearance()

    def open_settings(self) -> None:
        if self.window is None or self._busy:
            return
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()
        if self._settings_page is not None:
            if self._settings_page.close_intent == 1:
                self._reopen_settings_after_close = True
                return
            if self._settings_page.exit_pending:
                self._settings_page.cancel_exit()
            self.window.show_settings_page(self._settings_page)
            return
        # A direct or queued open has now reached the only point where it can
        # succeed. Consume the request here, never when merely scheduling it.
        self._reopen_settings_after_close = False
        dialog = SettingsDialog(self.config, parent=self.window)
        dialog.setWindowFlags(Qt.WindowType.Widget)
        dialog.setMinimumSize(0, 0)
        self._settings_page = dialog
        dialog.accepted.connect(self._apply_settings)
        dialog.exit_requested.connect(
            lambda result, page=dialog: self._begin_settings_exit(page, result)
        )
        dialog.finished.connect(
            lambda result, page=dialog: self._close_settings_page(page, result)
        )
        dialog.install_update_requested.connect(self.install_update)
        self.window.show_settings_page(dialog)

    def _begin_settings_exit(self, page: SettingsDialog, result: int) -> None:
        if self._settings_page is not page:
            page.complete_exit(result)
            return
        if self.window is None:
            page.complete_exit(result)
            return
        self.window.begin_settings_exit(
            page,
            lambda current=page, value=result: current.complete_exit(value),
        )

    def _close_settings_page(self, page: SettingsDialog, _result: int = 0) -> None:
        if self._settings_page is page:
            self._settings_page = None
        if self.window is not None:
            self.window.remove_settings_page(page)
        if self._reopen_settings_after_close:
            QTimer.singleShot(0, self._try_reopen_settings)

    def _try_reopen_settings(self) -> None:
        """Honor a queued reopen only when capture and teardown are both idle."""
        if (
            not self._reopen_settings_after_close
            or self._busy
            or self.window is None
            or self._settings_page is not None
        ):
            return
        self.open_settings()

    def _apply_settings(self) -> None:
        self._refresh_appearance()
        self.refresh_monitor_map()
        if self.cache is not None:
            self.cache.set_ttl(
                self.config.get("translation.cache_ttl_days", 30),
                self.config.get("translation.cache_max_entries", 2000),
            )
        self.ocr_engine = self._create_configured_ocr_engine()
        resolved_service = self._resolve_translator_service()
        if resolved_service != self.config.get("translation.service", "mock"):
            self.config.set("translation.service", resolved_service)
            self.config.save()
            self.set_status(f"检测到 API Key，已自动切换到 {resolved_service} 翻译服务")
        self.translator = create_translator(
            self.config.get("translation.service", "mock"),
            self.config.section("translation"),
            cache=self.cache,
            api_key_resolver=self.config.api_key,
        )
        if self.overlay_manager is not None:
            self.overlay_manager.apply_style()
        try:
            self._apply_hotkeys()
        except HotkeyError as exc:
            self.show_error("快捷键", str(exc))
        self._apply_autostart()
        if self.window is not None:
            self.window.reload_values()
        self.set_status("设置已保存")

    def apply_runtime_selection(
        self,
        ocr_engine: str | None = None,
        service: str | None = None,
        source: str | None = None,
        target: str | None = None,
    ) -> None:
        """主窗口下拉框变更时立即生效并保存。"""
        if ocr_engine:
            self.config.set("ocr.engine", ocr_engine)
        if service:
            self.config.set("translation.service", service)
            self.config.set("translation.auto_select_service", False)
        previous_source = str(
            self.config.get("translation.source_language", "auto") or "auto"
        )
        if source:
            self.config.set("translation.source_language", source)
            # The main window exposes the source language but not the advanced
            # OCR-language selector.  Keep them aligned when the user actually
            # changes the visible source selector; stale Chinese-only settings
            # must not silently poison English screenshots.
            if source != previous_source:
                self.config.set("ocr.lang", source)
                self.config.set("ocr.language_mode_version", 2)
        if target:
            self.config.set("translation.target_language", target)
        self.config.save()
        self.ocr_engine = self._create_configured_ocr_engine()
        self.translator = create_translator(
            self.config.get("translation.service", "mock"),
            self.config.section("translation"),
            cache=self.cache,
            api_key_resolver=self.config.api_key,
        )
        if self.window is not None:
            self.window.reload_values()
        self.set_status(
            f"已切换：OCR={self._active_ocr_engine_name} 翻译={self.config.get('translation.service')} "
            f"目标={self.config.get('translation.target_language')}"
        )

    def _create_configured_ocr_engine(self):
        """Resolve the requested backend without making a light build unbootable."""
        requested = str(self.config.get("ocr.engine", "windows") or "windows")
        candidates = list(dict.fromkeys((requested, "windows", "paddle", "none")))
        failures: list[str] = []
        for candidate in candidates:
            try:
                engine = create_ocr_engine(
                    candidate, self.config.section("ocr"), self.config
                )
            except (OCRUnavailableError, OSError, RuntimeError, ValueError) as exc:
                failures.append(f"{candidate}: {exc}")
                continue
            self._active_ocr_engine_name = candidate
            if candidate != requested:
                log.warning(
                    "OCR %s unavailable; using %s (%s)",
                    requested,
                    candidate,
                    "; ".join(failures),
                )
                # Keep the visible selectors and the engine actually executing
                # in agreement. This also migrates v0.1 users whose saved
                # Paddle choice is not present in the lightweight package.
                self.config.set("ocr.engine", candidate)
                try:
                    self.config.save()
                except OSError as exc:
                    log.warning("无法保存 OCR 回退选择：%s", exc)
            return engine
        raise OCRUnavailableError("没有可用的 OCR 引擎：" + "; ".join(failures))

    def _check_for_updates(self) -> None:
        if self._shutting_down or (
            self._update_check_task is not None and self._update_check_task.isRunning()
        ):
            return
        task = UpdateCheckTask(
            __version__,
            str(self.config.get("updates.repository", "nimbus-translate/screen-translator")),
            include_prereleases=bool(self.config.get("updates.include_prereleases", False)),
            parent=self.qt_app,
        )
        self._update_check_task = task
        task.updateFound.connect(self._update_available)
        task.failed.connect(lambda message: log.info("自动更新检查失败：%s", message))
        task.finished.connect(lambda current=task: self._update_check_finished(current))
        task.finished.connect(task.deleteLater)
        task.start()

    def _update_available(self, info) -> None:
        message = f"发现新版本 {info.latest_version}，可在设置中下载"
        self.set_status(message)
        if self.tray is not None:
            self.tray.showMessage("ScreenTranslator 更新", message)

    def _update_check_finished(self, task: UpdateCheckTask) -> None:
        if self._update_check_task is task:
            self._update_check_task = None

    def install_update(self, path: str, expected_sha256: str) -> None:
        """Launch only a verified package produced by UpdateDownloadTask."""
        candidate = Path(path).resolve()
        update_root = (app_data_dir() / "updates").resolve()
        if candidate.parent != update_root or candidate.suffix.lower() not in {".exe", ".msi"}:
            self.show_error("更新失败", "更新包路径无效")
            return
        if not candidate.is_file():
            self.show_error("更新失败", "更新包不存在")
            return
        if len(expected_sha256) != 64 or any(
            char not in "0123456789abcdefABCDEF" for char in expected_sha256
        ):
            self.show_error("更新失败", "更新包校验信息无效")
            return
        try:
            current_sha256 = sha256_file(candidate)
        except OSError as exc:
            self.show_error("更新失败", f"无法读取更新包：{exc}")
            return
        if current_sha256.casefold() != expected_sha256.casefold():
            self.show_error("更新失败", "更新包在下载后发生变化，已拒绝执行")
            return
        try:
            verify_authenticode(
                candidate,
                reference_path=runtime_signature_reference(),
            )
        except AuthenticodeVerificationError as exc:
            self.show_error("更新失败", f"更新包数字签名验证失败：{exc}")
            return
        try:
            arguments = [str(candidate)]
            if candidate.suffix.lower() == ".exe":
                arguments += ["/SP-", "/SILENT", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"]
            else:
                arguments = ["msiexec.exe", "/i", str(candidate), "/passive"]
            subprocess.Popen(arguments, close_fds=True)
        except OSError as exc:
            self.show_error("更新失败", f"无法启动安装程序：{exc}")
            return
        self.shutdown()

    def _resolve_translator_service(self) -> str:
        """启动时若当前是 mock 且检测到已配置的真实服务 Key，自动切换。"""
        service = str(self.config.get("translation.service", "mock"))
        if service != "mock" or not self.config.get("translation.auto_select_service", True):
            return service
        env_names = {
            "openai": "OPENAI_API_KEY",
            "deepl": "DEEPL_API_KEY",
            "google": "GOOGLE_TRANSLATE_API_KEY",
        }
        for candidate in ("openai", "deepl", "google"):
            if os.environ.get(env_names[candidate]) or self.config.api_key(candidate):
                return candidate
        return service

    def _apply_hotkeys(self) -> None:
        try:
            self.hotkey_manager.apply(self.config.hotkeys())
        except HotkeyError as exc:
            log.exception("快捷键注册失败")
            self.set_status(f"快捷键注册失败：{exc}")

    def _apply_autostart(self) -> None:
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            )
            if self.config.get("general.startup_with_system", False):
                exe = sys_executable()
                winreg.SetValueEx(key, "ScreenTranslator", 0, winreg.REG_SZ, f'"{exe}" "{main_script()}"')
            else:
                try:
                    winreg.DeleteValue(key, "ScreenTranslator")
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
        except Exception as exc:
            log.warning("开机启动设置失败：%s", exc)

    # ------------------------------------------------------------------ status/history
    def set_status(self, text: str) -> None:
        log.info("状态：%s", text)
        if self.window is not None:
            self.window.set_status(text)
        if self.tray is not None:
            self.tray.set_tooltip(text)

    def show_error(self, title: str, message: str) -> None:
        self.set_status(f"{title}：{message}")
        log.error("%s：%s", title, message)
        if self.window is not None:
            if not self.window.isVisible() or self.window.isMinimized():
                self.window.showNormal()
            self.window.raise_()
            self.window.activateWindow()
            QMessageBox.warning(self.window, title, message)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        if self.window is not None:
            self.window.set_busy(busy)
        if self.tray is not None:
            self.tray.set_busy(busy)
        if self._settings_page is not None:
            self._settings_page.setEnabled(not busy)
        if not busy and self._reopen_settings_after_close:
            QTimer.singleShot(0, self._try_reopen_settings)

    def _save_history(self, capture: CaptureInfo, regions: list[TextRegion]) -> None:
        if not self.config.get("general.save_history", False):
            return
        try:
            history_dir = Path(self.config.get("general.history_dir", "") or "history")
            history_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            capture_path = history_dir / f"capture_{stamp}.png"
            from PIL import Image

            rgb = capture.image[:, :, ::-1]
            Image.fromarray(rgb, mode="RGB").save(capture_path, format="PNG")
            record = {"capture": capture_path.name, "regions": [r.to_dict() for r in regions]}
            (history_dir / f"regions_{stamp}.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            log.warning("保存历史失败：%s", exc)


def sys_executable() -> str:
    return sys.executable


def main_script() -> str:
    if getattr(sys, "frozen", False):
        return sys.executable
    return os.path.abspath(sys.argv[0])
