"""屏幕截图服务（mss 实现，物理像素坐标）。"""

from __future__ import annotations

import threading
from typing import Any

from app.models import CaptureInfo
from utils import dpi_utils
from utils.image_utils import mss_bgra_to_bgr


class ScreenshotService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sct: Any = None

    def _get_sct(self):
        if self._sct is None:
            import mss

            self._sct = mss.mss()
        return self._sct

    def monitors(self) -> list[dict]:
        return list(self._get_sct().monitors)

    def virtual_screen_bbox(self) -> tuple[int, int, int, int]:
        monitor = self._get_sct().monitors[0]
        return (monitor["left"], monitor["top"], monitor["left"] + monitor["width"], monitor["top"] + monitor["height"])

    def capture_bbox(self, bbox: tuple[int, int, int, int]) -> CaptureInfo:
        left, top, right, bottom = bbox
        width = max(1, right - left)
        height = max(1, bottom - top)
        with self._lock:
            shot = self._get_sct().grab({"left": left, "top": top, "width": width, "height": height})
            image = mss_bgra_to_bgr(shot.bgra, shot.width, shot.height)
        monitor_indices = self._monitors_in_bbox(bbox)
        return CaptureInfo(
            image=image,
            bbox=(left, top, left + width, top + height),
            monitor_indices=monitor_indices,
            mode="region",
        )

    def capture_fullscreen(self) -> CaptureInfo:
        bbox = self.virtual_screen_bbox()
        capture = self.capture_bbox(bbox)
        capture.mode = "fullscreen"
        return capture

    def capture_window(self, hwnd: int) -> CaptureInfo:
        from services.window_capture_service import (
            WindowCaptureError,
            capture_window_bgr,
            get_window_rect_physical,
            is_window_capturable,
        )

        if not is_window_capturable(hwnd):
            raise WindowCaptureError("目标窗口不可见、已最小化或已经关闭")
        rect = get_window_rect_physical(hwnd)
        virtual = self.virtual_screen_bbox()
        from utils.dpi_utils import intersect

        clamped = intersect(rect, virtual)
        if clamped is None:
            raise WindowCaptureError("目标窗口不在当前桌面可见范围内")

        try:
            image, native_rect = capture_window_bgr(hwnd)
            native_clamped = intersect(native_rect, virtual)
            if native_clamped is None:
                raise WindowCaptureError("目标窗口不在当前桌面可见范围内")
            left = native_clamped[0] - native_rect[0]
            top = native_clamped[1] - native_rect[1]
            right = left + native_clamped[2] - native_clamped[0]
            bottom = top + native_clamped[3] - native_clamped[1]
            image = image[top:bottom, left:right].copy()
            if image.size == 0:
                raise WindowCaptureError("目标窗口没有可捕获的像素")
            # Some GPU/protected surfaces report PrintWindow success but return
            # a fully black buffer. Treat only the degenerate all-black case as
            # failure so the validated screen crop can recover visible content.
            if int(image.max()) <= 1:
                raise WindowCaptureError("原生窗口捕获返回了空白画面")
            capture = CaptureInfo(
                image=image,
                bbox=native_clamped,
                monitor_indices=self._monitors_in_bbox(native_clamped),
                mode="window",
            )
        except WindowCaptureError:
            # Some GPU/protected windows reject PrintWindow. A visible screen crop
            # remains safe here because the target rectangle was fully validated;
            # it must never fall back to the whole virtual desktop.
            if not is_window_capturable(hwnd):
                raise WindowCaptureError("目标窗口在捕获前已经关闭或隐藏")
            current_rect = get_window_rect_physical(hwnd)
            current_clamped = intersect(current_rect, virtual)
            if current_clamped is None:
                raise WindowCaptureError("目标窗口已移出当前桌面可见范围")
            capture = self.capture_bbox(current_clamped)
        capture.mode = "window"
        return capture

    def _monitors_in_bbox(self, bbox: tuple[int, int, int, int]) -> list[int]:
        physical = dpi_utils.enum_display_monitors_physical()
        indices: list[int] = []
        for idx, monitor in enumerate(physical):
            inter = dpi_utils.intersect(bbox, monitor)
            if inter is not None and (inter[2] - inter[0]) * (inter[3] - inter[1]) > 0:
                indices.append(idx)
        return indices
