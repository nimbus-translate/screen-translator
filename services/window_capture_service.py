"""Platform boundary for current-window discovery and native capture."""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

import numpy as np


class WindowCaptureError(RuntimeError):
    pass


IS_WINDOWS = sys.platform == "win32"
_DWMWA_EXTENDED_FRAME_BOUNDS = 9
_PW_RENDERFULLCONTENT = 0x00000002
_DIB_RGB_COLORS = 0
_BI_RGB = 0

if IS_WINDOWS:
    _user32 = ctypes.windll.user32
    _dwmapi = ctypes.windll.dwmapi
    _gdi32 = ctypes.windll.gdi32
else:  # Keep the application importable on macOS/Linux.
    _user32 = None
    _dwmapi = None
    _gdi32 = None


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", ctypes.c_ushort),
        ("biBitCount", ctypes.c_ushort),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class _RGBQUAD(ctypes.Structure):
    _fields_ = [
        ("rgbBlue", ctypes.c_ubyte),
        ("rgbGreen", ctypes.c_ubyte),
        ("rgbRed", ctypes.c_ubyte),
        ("rgbReserved", ctypes.c_ubyte),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", _RGBQUAD * 1)]


def _configure_winapi_signatures() -> None:
    """Keep pointer-sized USER/GDI handles intact on 64-bit Python."""
    if not IS_WINDOWS:
        return

    _user32.GetForegroundWindow.argtypes = []
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.IsWindow.argtypes = [wintypes.HWND]
    _user32.IsWindow.restype = wintypes.BOOL
    _user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindowTextLengthW.restype = ctypes.c_int
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowTextW.restype = ctypes.c_int
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsWindowVisible.restype = wintypes.BOOL
    _user32.IsIconic.argtypes = [wintypes.HWND]
    _user32.IsIconic.restype = wintypes.BOOL
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
    _user32.GetWindowRect.restype = wintypes.BOOL
    _user32.GetDC.argtypes = [wintypes.HWND]
    _user32.GetDC.restype = wintypes.HDC
    _user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    _user32.ReleaseDC.restype = ctypes.c_int
    _user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    _user32.PrintWindow.restype = wintypes.BOOL

    _dwmapi.DwmGetWindowAttribute.argtypes = [
        wintypes.HWND,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long

    _gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    _gdi32.CreateCompatibleDC.restype = wintypes.HDC
    _gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    _gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    _gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    _gdi32.SelectObject.restype = wintypes.HGDIOBJ
    _gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    _gdi32.DeleteObject.restype = wintypes.BOOL
    _gdi32.DeleteDC.argtypes = [wintypes.HDC]
    _gdi32.DeleteDC.restype = wintypes.BOOL
    _gdi32.GetDIBits.argtypes = [
        wintypes.HDC,
        wintypes.HBITMAP,
        wintypes.UINT,
        wintypes.UINT,
        wintypes.LPVOID,
        ctypes.POINTER(_BITMAPINFO),
        wintypes.UINT,
    ]
    _gdi32.GetDIBits.restype = ctypes.c_int


_configure_winapi_signatures()

_HGDI_ERROR = ctypes.c_void_p(-1).value


def _valid_gdi_handle(handle) -> bool:
    return bool(handle) and handle != _HGDI_ERROR


def window_capture_available() -> bool:
    return IS_WINDOWS and _user32 is not None


def get_foreground_window() -> int:
    if not window_capture_available():
        return 0
    return int(_user32.GetForegroundWindow() or 0)


def is_window(hwnd: int) -> bool:
    return bool(window_capture_available() and hwnd and _user32.IsWindow(hwnd))


def get_window_pid(hwnd: int) -> int:
    if not is_window(hwnd):
        return 0
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def is_current_process_window(hwnd: int) -> bool:
    return bool(hwnd and get_window_pid(hwnd) == os.getpid())


def get_window_title(hwnd: int) -> str:
    if not is_window(hwnd):
        return ""
    length = int(_user32.GetWindowTextLengthW(hwnd))
    buffer = ctypes.create_unicode_buffer(max(1, length + 1))
    _user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value


def is_window_visible(hwnd: int) -> bool:
    return bool(is_window(hwnd) and _user32.IsWindowVisible(hwnd))


def is_window_minimized(hwnd: int) -> bool:
    return bool(is_window(hwnd) and _user32.IsIconic(hwnd))


def get_window_rect_physical(hwnd: int) -> tuple[int, int, int, int]:
    """Return a validated physical-pixel rectangle for a live window."""
    if not is_window(hwnd):
        raise WindowCaptureError("目标窗口已经关闭")
    rect = _RECT()
    result = -1
    try:
        result = _dwmapi.DwmGetWindowAttribute(
            hwnd,
            _DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
    except Exception:
        result = -1
    if result != 0 and not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise WindowCaptureError("无法读取目标窗口边界")
    value = (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
    if value[2] <= value[0] or value[3] <= value[1]:
        raise WindowCaptureError("目标窗口没有可捕获的区域")
    return value


def is_window_capturable(hwnd: int) -> bool:
    if not is_window_visible(hwnd) or is_window_minimized(hwnd):
        return False
    try:
        get_window_rect_physical(hwnd)
    except WindowCaptureError:
        return False
    return True


def capture_window_bgr(hwnd: int) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Capture a window through PrintWindow; callers may safely screen-crop on failure."""
    if not is_window_capturable(hwnd):
        raise WindowCaptureError("目标窗口不可见、已最小化或已经关闭")
    rect = get_window_rect_physical(hwnd)
    width, height = rect[2] - rect[0], rect[3] - rect[1]
    screen_dc = _user32.GetDC(None)
    memory_dc = bitmap = previous = 0
    bitmap_selected = False
    try:
        if not screen_dc:
            raise WindowCaptureError("无法创建窗口捕获上下文")
        memory_dc = _gdi32.CreateCompatibleDC(screen_dc)
        bitmap = _gdi32.CreateCompatibleBitmap(screen_dc, width, height)
        if not memory_dc or not bitmap:
            raise WindowCaptureError("无法创建窗口捕获缓冲区")
        previous = _gdi32.SelectObject(memory_dc, bitmap)
        if not _valid_gdi_handle(previous):
            raise WindowCaptureError("无法选择窗口捕获缓冲区")
        bitmap_selected = True
        rendered = bool(_user32.PrintWindow(hwnd, memory_dc, _PW_RENDERFULLCONTENT))
        if not rendered:
            rendered = bool(_user32.PrintWindow(hwnd, memory_dc, 0))
        if not rendered:
            raise WindowCaptureError("该窗口不支持原生内容捕获")

        info = _BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height  # top-down DIB
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = _BI_RGB
        buffer = (ctypes.c_ubyte * (width * height * 4))()

        # GetDIBits requires the bitmap not to be selected into any DC.
        restored = _gdi32.SelectObject(memory_dc, previous)
        if not _valid_gdi_handle(restored):
            raise WindowCaptureError("无法读取窗口捕获缓冲区")
        bitmap_selected = False
        previous = 0
        rows = _gdi32.GetDIBits(
            memory_dc,
            bitmap,
            0,
            height,
            ctypes.byref(buffer),
            ctypes.byref(info),
            _DIB_RGB_COLORS,
        )
        if rows != height:
            raise WindowCaptureError("读取窗口像素失败")
        bgra = np.frombuffer(buffer, dtype=np.uint8).reshape((height, width, 4))
        return bgra[:, :, :3].copy(), rect
    finally:
        if bitmap_selected and previous and memory_dc:
            restored = _gdi32.SelectObject(memory_dc, previous)
            bitmap_selected = not _valid_gdi_handle(restored)
        # If restoring the stock bitmap failed, deleting the DC first releases
        # its selection so DeleteObject can still reclaim our bitmap.
        if bitmap_selected and memory_dc:
            _gdi32.DeleteDC(memory_dc)
            memory_dc = 0
            bitmap_selected = False
        if bitmap:
            _gdi32.DeleteObject(bitmap)
        if memory_dc:
            _gdi32.DeleteDC(memory_dc)
        if screen_dc:
            _user32.ReleaseDC(None, screen_dc)
