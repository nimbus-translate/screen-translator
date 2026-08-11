"""快捷键归一化测试。"""

from app.hotkeys import normalize_hotkey


def test_normalize_hotkey_brackets():
    assert normalize_hotkey("Ctrl+Shift+A") == "<ctrl>+<shift>+a"
    assert normalize_hotkey("ctrl+shift+f") == "<ctrl>+<shift>+f"
    assert normalize_hotkey("Alt+F4") == "<alt>+<f4>"
    assert normalize_hotkey("F12") == "<f12>"
    assert normalize_hotkey("Ctrl+Shift+H, Ctrl+Alt+H") == "<ctrl>+<shift>+h"


def test_normalize_hotkey_idempotent():
    once = normalize_hotkey("Ctrl+Shift+A")
    twice = normalize_hotkey(once)
    assert once == twice == "<ctrl>+<shift>+a"
    assert normalize_hotkey("<ctrl>+<shift>+a") == "<ctrl>+<shift>+a"
