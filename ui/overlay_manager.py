"""覆盖层管理器：按显示器分发覆盖窗口，负责 DPI 坐标换算。"""

from __future__ import annotations

from PySide6.QtCore import QObject, QRectF
from PySide6.QtGui import QColor

from app.config import AppConfig
from app.logger import get_logger
from app.models import CaptureInfo, TextRegion
from ui.translation_overlay import Block, TranslationOverlayWindow
from utils import dpi_utils

log = get_logger("overlay.manager")


class OverlayManager(QObject):
    def __init__(self, config: AppConfig, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._monitors: list[dpi_utils.MonitorInfo] = []
        self._windows: dict[int, TranslationOverlayWindow] = {}
        self._visible = True
        self._edit_mode = False

    def set_monitor_map(self, monitors: list[dpi_utils.MonitorInfo]) -> None:
        self._monitors = monitors

    # ------------------------------------------------------------- capture result
    def show_regions(self, capture: CaptureInfo, regions: list[TextRegion]) -> bool:
        if not self._monitors:
            return False
        groups: dict[int, list[TextRegion]] = {}
        for region in regions:
            monitor = dpi_utils.monitor_for_physical_point(region.center[0], region.center[1], self._monitors)
            groups.setdefault(monitor.index, []).append(region)

        self.hide_all()
        shown = False
        for monitor in self._monitors:
            regions_on_monitor = groups.get(monitor.index)
            if not regions_on_monitor:
                continue
            mon_bbox = dpi_utils.clamp_bbox_to_monitor(capture.bbox, monitor)
            geo = dpi_utils.physical_rect_to_overlay_geometry(mon_bbox, monitor)
            blocks: list[Block] = []
            for region in regions_on_monitor:
                display_text = (region.translated_text or region.text).strip()
                # 翻译失败时服务会原样返回原文。不要再拿覆盖层重绘一遍，
                # 否则干净的原字会被换成不同字号和背景，整页看起来发脏。
                if not display_text or display_text == region.text.strip():
                    continue
                local = dpi_utils.physical_rect_to_local(
                    (region.x, region.y, region.right, region.bottom), mon_bbox, monitor
                )
                blocks.append(
                    Block(
                        QRectF(*local),
                        display_text,
                        QColor(region.text_color),
                        region.background_color,
                    )
                )
            if not blocks:
                continue

            window = self._windows.get(monitor.index)
            if window is None:
                window = TranslationOverlayWindow(self._config)
                window.close_requested.connect(self._on_overlay_close_requested)
                self._windows[monitor.index] = window

            window.setGeometry(geo[0], geo[1], max(1, geo[2]), max(1, geo[3]))
            window.set_blocks(blocks)
            window.set_edit_mode(self._edit_mode)
            window.show_fade()
            window.raise_()
            shown = True
        self._visible = shown
        return shown

    def _on_overlay_close_requested(self) -> None:
        self.hide_all()
        self._visible = False
        self.set_edit_mode(False)

    # ------------------------------------------------------------- visibility
    def hide_all(self, animate: bool = False) -> None:
        for window in self._windows.values():
            if animate:
                window.hide_fade()
            else:
                window.hide()
        self._visible = False

    def show_all(self) -> None:
        for window in self._windows.values():
            if window._blocks:
                window.show_fade()
                window.raise_()
        self._visible = True

    def clear_all(self) -> None:
        """立即清除所有覆盖窗口内容并隐藏，保证下一次翻译不叠加。"""
        for window in self._windows.values():
            window.clear()
            window.hide()
        self._visible = False

    def toggle(self) -> bool:
        if self._visible:
            self.hide_all(animate=True)
        else:
            self.show_all()
        return self._visible

    def is_visible(self) -> bool:
        return self._visible

    # ------------------------------------------------------------- edit/style
    def set_edit_mode(self, enabled: bool) -> None:
        self._edit_mode = enabled
        for window in self._windows.values():
            window.set_edit_mode(enabled)
        if self._visible:
            for window in self._windows.values():
                if window._blocks:
                    window.show()
                    window.raise_()

    def apply_style(self) -> None:
        for window in self._windows.values():
            window.apply_style()
