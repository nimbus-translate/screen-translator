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
        from services.window_capture_service import get_window_rect_physical

        rect = get_window_rect_physical(hwnd)
        virtual = self.virtual_screen_bbox()
        from utils.dpi_utils import intersect

        clamped = intersect(rect, virtual) or virtual
        capture = self.capture_bbox(clamped)
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
