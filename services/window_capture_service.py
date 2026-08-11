"""Windows 窗口捕获：前台窗口、窗口物理矩形、进程归属。"""

from __future__ import annotations

import ctypes
import os

_user32 = ctypes.windll.user32
_dwmapi = ctypes.windll.dwmapi

_DWMWA_EXTENDED_FRAME_BOUNDS = 9


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def get_foreground_window() -> int:
    return int(_user32.GetForegroundWindow() or 0)


def get_window_pid(hwnd: int) -> int:
    pid = ctypes.c_ulong()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def is_current_process_window(hwnd: int) -> bool:
    return get_window_pid(hwnd) == os.getpid()


def get_window_title(hwnd: int) -> str:
    length = _user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def get_window_rect_physical(hwnd: int) -> tuple[int, int, int, int]:
    """返回窗口物理像素矩形，优先用 DWM 扩展边框（不含阴影）。"""
    rect = _RECT()
    try:
        if _dwmapi.DwmGetWindowAttribute(
            hwnd, _DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)
        ) == 0:
            return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        pass
    _user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return (rect.left, rect.top, rect.right, rect.bottom)


def is_window_visible(hwnd: int) -> bool:
    return bool(_user32.IsWindowVisible(hwnd))
