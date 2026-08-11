"""应用控制器：把截图、OCR、翻译、覆盖层、托盘、快捷键串起来。"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QRect, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QMessageBox, QWidget

from app.config import AppConfig
from app.hotkeys import HotkeyError, HotkeyManager
from app.logger import get_logger
from app.models import CaptureInfo, TextRegion

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

from services.ocr.base import create_ocr_engine, list_ocr_engines
from services.screenshot_service import ScreenshotService
from services.translation.base import Translator
from services.translation.cache import TranslationCache
from services.translation.factory import create_translator, list_translators
from services.window_capture_service import get_foreground_window, is_current_process_window, is_window_visible
from ui.main_window import MainWindow
from ui.floating_status import FloatingStatus
from ui.overlay_manager import OverlayManager
from ui.selection_overlay import SelectionOverlay
from ui.settings_dialog import SettingsDialog
from ui.style import apply_style
from ui.tray_icon import TrayIcon, build_icon
from utils import dpi_utils
from utils.language_utils import LANGUAGES, LANGUAGE_CODES
from workers.translation_worker import PipelineTask

log = get_logger("application")


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
        self.cache: TranslationCache | None = None

        self.window: MainWindow | None = None
        self.tray: TrayIcon | None = None
        self.selection: SelectionOverlay | None = None
        self.worker: PipelineTask | None = None

        self._last_capture: CaptureInfo | None = None
        self._overlay_visible = True
        self._busy = False
        self._last_external_hwnd = 0
        self._foreground_tracker = QTimer(self)
        self._foreground_tracker.setInterval(200)
        self._foreground_tracker.timeout.connect(self._remember_external_foreground)

        self.hotkey_manager.triggered.connect(self.on_hotkey)

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        try:
            apply_style(self.qt_app)
            self.qt_app.setWindowIcon(build_icon())
            self._build_monitor_map()
            self.cache = TranslationCache(
                path=Path(self.config.path.parent) / "translation_cache.json",
                ttl_days=self.config.get("translation.cache_ttl_days", 30),
                max_entries=self.config.get("translation.cache_max_entries", 2000),
            )
            self.ocr_engine = create_ocr_engine(
                self.config.get("ocr.engine", "paddle"), self.config.section("ocr"), self.config
            )
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
            self.window.show()
            self.tray.show()
            self._remember_external_foreground()
            self._foreground_tracker.start()
            self._apply_hotkeys()
            self._apply_autostart()
            self._warmup()
        except Exception as exc:
            log.exception("应用启动失败")
            QMessageBox.critical(None, "启动失败", f"{exc}")

    def shutdown(self) -> None:
        self._foreground_tracker.stop()
        self.hotkey_manager.stop()
        if self.worker is not None:
            self.worker.cancel()
            self.worker.wait(2000)
        if self.overlay_manager is not None:
            self.overlay_manager.hide_all()
        if self.cache is not None:
            self.cache.flush()
        self.qt_app.quit()

    def _warmup(self) -> None:
        """后台线程预热 OCR 模型，避免第一次截图时卡很久。"""
        if os.environ.get("SCREEN_TRANSLATOR_NO_WARMUP") == "1" or os.environ.get(
            "SCREEN_TRANSLATOR_SELFTEST"
        ) == "1":
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
            if hwnd and not is_current_process_window(hwnd) and is_window_visible(hwnd):
                self._last_external_hwnd = hwnd
        except Exception:
            pass

    # ------------------------------------------------------------------ dpi/monitor
    def _build_monitor_map(self) -> None:
        self.monitor_map = dpi_utils.build_monitor_map(
            self.qt_app.screens(), dpi_utils.enum_display_monitors_physical()
        )

    def refresh_monitor_map(self) -> None:
        self._build_monitor_map()

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
        self._set_busy(True)
        self.overlay_manager.hide_all()
        if self.window.isVisible():
            self.window.showMinimized()
        QTimer.singleShot(200, self._show_selection)

    def _show_selection(self) -> None:
        if self.selection is not None:
            self.selection.close()
        self.selection = SelectionOverlay(
            self.qt_app.primaryScreen().virtualGeometry(),
            mask_opacity=int(self.config.get("capture.select_mask_opacity", 100)),
            border_color=str(self.config.get("capture.select_border_color", "#FF3B30")),
        )
        self.selection.selection_done.connect(self._on_selection_done)
        self.selection.cancelled.connect(self._on_selection_cancelled)
        self.selection.show()
        self.selection.raise_()
        self.selection.activateWindow()
        self.selection.setFocus()

    def _on_selection_done(self, logical_rect: QRect) -> None:
        if self.selection is not None:
            self.selection.close()
            self.selection = None
        physical = dpi_utils.logical_rect_to_physical_union(logical_rect, self.monitor_map)
        self._set_busy(False)
        if physical is None or physical[2] - physical[0] < 4 or physical[3] - physical[1] < 4:
            self.set_status("框选区域太小，已取消")
            return
        self.run_capture_rect(physical, mode="region")

    def _on_selection_cancelled(self) -> None:
        if self.selection is not None:
            self.selection.close()
            self.selection = None
        self._set_busy(False)
        self.set_status("已取消截图")

    def start_capture(self, mode: str) -> None:
        if self._busy or self.window is None or self.overlay_manager is None:
            return
        self._set_busy(True)
        self.overlay_manager.hide_all()
        if mode == "fullscreen" and self.window.isVisible():
            self.window.showMinimized()
        QTimer.singleShot(150, lambda: self._do_capture(mode))

    def _do_capture(self, mode: str) -> None:
        try:
            if mode == "fullscreen":
                capture = self.screenshot_service.capture_fullscreen()
            elif mode == "window":
                hwnd = self._last_external_hwnd or get_foreground_window()
                if not hwnd or is_current_process_window(hwnd):
                    self.set_status("请先切换到要翻译的窗口")
                    self._set_busy(False)
                    return
                capture = self.screenshot_service.capture_window(hwnd)
            else:
                self._set_busy(False)
                return
        except Exception as exc:
            log.exception("截图失败")
            self.show_error("截图失败", f"{exc}")
            self._set_busy(False)
            return
        self._set_busy(False)
        self.run_capture_rect(capture.bbox, mode=mode, existing=capture)

    def run_capture_rect(
        self, bbox: tuple[int, int, int, int], mode: str, existing: CaptureInfo | None = None
    ) -> None:
        if self._busy:
            return
        self._set_busy(True)
        # 清除上一次的覆盖层，保证不叠加
        if self.overlay_manager is not None:
            self.overlay_manager.clear_all()
        self.floating_status.show_fade("正在翻译…")
        try:
            if existing is not None:
                capture = existing
            else:
                capture = self.screenshot_service.capture_bbox(bbox)
        except Exception as exc:
            log.exception("截图失败")
            self.show_error("截图失败", f"{exc}")
            self._set_busy(False)
            return

        self._last_capture = capture
        self._cancel_worker()
        self.worker = PipelineTask(
            capture=capture,
            ocr_engine=self.ocr_engine,
            translator=self.translator,
            config=self.config,
        )
        self.worker.status.connect(self.set_status)
        self.worker.status.connect(self._on_worker_status)
        self.worker.error.connect(lambda msg: self.show_error("处理失败", msg))
        self.worker.result.connect(self.on_pipeline_result)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.start()

    def _on_worker_status(self, text: str) -> None:
        self.floating_status.show_fade(text)

    def _on_worker_finished(self) -> None:
        self._set_busy(False)
        self.floating_status.hide_fade(delay_ms=1200)

    def _cancel_worker(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(1000)
        self.worker = None

    def on_pipeline_result(self, payload: dict) -> None:
        capture: CaptureInfo = payload["capture"]
        regions: list[TextRegion] = payload["regions"]
        failed_count = int(payload.get("failed_count", 0))
        if self.overlay_manager is None:
            return
        if not regions:
            self.show_error("翻译完成", "没有识别到可翻译的文字")
            return
        self.overlay_manager.show_regions(capture, regions)
        self._overlay_visible = True
        if self.tray is not None:
            self.tray.set_overlay_checked(True)
        if failed_count:
            self.set_status(f"翻译服务限流：已显示 {len(regions)} 个原文块，请稍后重试或切换翻译服务")
        else:
            self.set_status(f"翻译完成：{len(regions)} 个文本块")
        self._save_history(capture, regions)

    # ------------------------------------------------------------------ overlay
    def toggle_overlay(self) -> None:
        if self.overlay_manager is None:
            return
        self._overlay_visible = not self._overlay_visible
        if self._overlay_visible:
            self.overlay_manager.show_all()
        else:
            self.overlay_manager.hide_all(animate=True)
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
        self.run_capture_rect(self._last_capture.bbox, mode=self._last_capture.mode)

    def set_edit_mode(self, enabled: bool) -> None:
        if self.overlay_manager is None:
            return
        self.overlay_manager.set_edit_mode(enabled)
        if self.window is not None:
            self.window.set_edit_mode_checked(enabled)

    # ------------------------------------------------------------------ settings
    def open_settings(self) -> None:
        if self.window is None:
            return
        dialog = SettingsDialog(self.config, parent=self.window)
        dialog._embedded = True
        dialog.setWindowFlags(Qt.WindowType.Widget)
        dialog.accepted.connect(self._apply_settings)
        dialog.finished.connect(lambda _result: self.window.close_settings_page(dialog))
        self.window.show_settings_page(dialog)
        dialog.show()

    def _apply_settings(self) -> None:
        self._build_monitor_map()
        if self.overlay_manager is not None:
            self.overlay_manager.set_monitor_map(self.monitor_map)
        if self.cache is not None:
            self.cache.set_ttl(
                self.config.get("translation.cache_ttl_days", 30),
                self.config.get("translation.cache_max_entries", 2000),
            )
        self.ocr_engine = create_ocr_engine(
            self.config.get("ocr.engine", "paddle"), self.config.section("ocr"), self.config
        )
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
        if source:
            self.config.set("translation.source_language", source)
        if target:
            self.config.set("translation.target_language", target)
        self.config.save()
        self.ocr_engine = create_ocr_engine(
            self.config.get("ocr.engine", "paddle"), self.config.section("ocr"), self.config
        )
        self.translator = create_translator(
            self.config.get("translation.service", "mock"),
            self.config.section("translation"),
            cache=self.cache,
            api_key_resolver=self.config.api_key,
        )
        self.set_status(
            f"已切换：OCR={self.config.get('ocr.engine')} 翻译={self.config.get('translation.service')} "
            f"目标={self.config.get('translation.target_language')}"
        )

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
            QMessageBox.warning(self.window, title, message)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        if self.window is not None:
            self.window.set_busy(busy)

    def _save_history(self, capture: CaptureInfo, regions: list[TextRegion]) -> None:
        if not self.config.get("general.save_history", False):
            return
        try:
            history_dir = Path(self.config.get("general.history_dir", "") or "history")
            history_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            capture_path = history_dir / f"capture_{stamp}.png"
            import cv2

            cv2.imwrite(str(capture_path), capture.image)
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
