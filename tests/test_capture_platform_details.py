"""Regression tests for Win32 handle widths and reduced status motion."""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import services.window_capture_service as window_capture
from ui.floating_status import FloatingStatus


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 API signatures")
def test_winapi_handle_signatures_are_pointer_sized():
    assert window_capture._user32.GetForegroundWindow.restype is wintypes.HWND
    assert window_capture._user32.GetDC.restype is wintypes.HDC
    assert window_capture._user32.PrintWindow.argtypes[:2] == [
        wintypes.HWND,
        wintypes.HDC,
    ]
    assert window_capture._gdi32.CreateCompatibleDC.restype is wintypes.HDC
    assert window_capture._gdi32.CreateCompatibleBitmap.restype is wintypes.HBITMAP
    assert window_capture._gdi32.SelectObject.restype is wintypes.HGDIOBJ
    assert ctypes.sizeof(window_capture._user32.GetForegroundWindow.restype) == ctypes.sizeof(
        ctypes.c_void_p
    )


class _FakeUser32:
    def __init__(self, events, screen_dc: int, rendered: bool = True) -> None:
        self.events = events
        self.screen_dc = screen_dc
        self.rendered = rendered

    def GetDC(self, hwnd):
        self.events.append(("get_dc", hwnd))
        return self.screen_dc

    def PrintWindow(self, hwnd, dc, flags):
        self.events.append(("print_window", hwnd, dc, flags))
        return self.rendered

    def ReleaseDC(self, hwnd, dc):
        self.events.append(("release_dc", hwnd, dc))
        return 1


class _FakeGDI32:
    def __init__(self, events, memory_dc: int, bitmap: int, previous: int) -> None:
        self.events = events
        self.memory_dc = memory_dc
        self.bitmap = bitmap
        self.previous = previous
        self._select_count = 0

    def CreateCompatibleDC(self, dc):
        self.events.append(("create_dc", dc))
        return self.memory_dc

    def CreateCompatibleBitmap(self, dc, width, height):
        self.events.append(("create_bitmap", dc, width, height))
        return self.bitmap

    def SelectObject(self, dc, obj):
        self.events.append(("select", dc, obj))
        self._select_count += 1
        return self.previous if self._select_count == 1 else self.bitmap

    def GetDIBits(self, dc, bitmap, first, rows, buffer, info, usage):
        self.events.append(("get_dibits", dc, bitmap, first, rows, usage))
        return rows

    def DeleteObject(self, obj):
        self.events.append(("delete_object", obj))
        return 1

    def DeleteDC(self, dc):
        self.events.append(("delete_dc", dc))
        return 1


def _install_fake_capture_apis(monkeypatch, *, rendered: bool):
    events = []
    handles = {
        "hwnd": 0x1_0000_0101,
        "screen_dc": 0x1_0000_0202,
        "memory_dc": 0x1_0000_0303,
        "bitmap": 0x1_0000_0404,
        "previous": 0x1_0000_0505,
    }
    monkeypatch.setattr(
        window_capture,
        "_user32",
        _FakeUser32(events, handles["screen_dc"], rendered=rendered),
    )
    monkeypatch.setattr(
        window_capture,
        "_gdi32",
        _FakeGDI32(
            events,
            handles["memory_dc"],
            handles["bitmap"],
            handles["previous"],
        ),
    )
    monkeypatch.setattr(window_capture, "is_window_capturable", lambda _hwnd: True)
    monkeypatch.setattr(
        window_capture,
        "get_window_rect_physical",
        lambda _hwnd: (10, 20, 12, 22),
    )
    return events, handles


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 resource lifecycle")
def test_print_window_keeps_64_bit_handles_and_deselects_before_read(monkeypatch):
    events, handles = _install_fake_capture_apis(monkeypatch, rendered=True)

    image, rect = window_capture.capture_window_bgr(handles["hwnd"])

    assert rect == (10, 20, 12, 22)
    assert image.shape == (2, 2, 3)
    assert ("print_window", handles["hwnd"], handles["memory_dc"], 2) in events
    restore = ("select", handles["memory_dc"], handles["previous"])
    read = ("get_dibits", handles["memory_dc"], handles["bitmap"], 0, 2, 0)
    assert events.index(restore) < events.index(read)
    assert events[-3:] == [
        ("delete_object", handles["bitmap"]),
        ("delete_dc", handles["memory_dc"]),
        ("release_dc", None, handles["screen_dc"]),
    ]


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 resource lifecycle")
def test_print_window_failure_releases_every_owned_gdi_resource(monkeypatch):
    events, handles = _install_fake_capture_apis(monkeypatch, rendered=False)

    with pytest.raises(window_capture.WindowCaptureError):
        window_capture.capture_window_bgr(handles["hwnd"])

    assert ("select", handles["memory_dc"], handles["previous"]) in events
    assert events[-3:] == [
        ("delete_object", handles["bitmap"]),
        ("delete_dc", handles["memory_dc"]),
        ("release_dc", None, handles["screen_dc"]),
    ]


def test_reduced_motion_never_starts_status_dot_timer(qapp, monkeypatch):
    monkeypatch.setenv("SCREEN_TRANSLATOR_MOTION", "reduced")
    status = FloatingStatus()

    status.show_fade("第一次")
    qapp.processEvents()
    assert status.isVisible()
    assert not status._timer.isActive()

    # Exercise the already-visible fast path as well; this used to restart it.
    status.show_fade("第二次")
    start_phase = status._phase
    QTest.qWait(180)
    assert not status._timer.isActive()
    assert status._phase == start_phase == 0

    status.hide_immediate()
    status.deleteLater()
