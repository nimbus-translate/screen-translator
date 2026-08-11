"""全局快捷键管理（pynput 实现，Qt 信号转发到主线程）。"""

from __future__ import annotations

import re
from typing import Callable

from PySide6.QtCore import QObject, Signal

from app.logger import get_logger

log = get_logger("hotkeys")


class HotkeyError(Exception):
    pass


def normalize_hotkey(sequence: str) -> str:
    """把 'Ctrl+Shift+A' 统一成 pynput 语法：修饰键用 <ctrl> 形式。"""
    sequence = sequence.strip().split(",")[0].strip()
    parts_out = []
    for raw in re.split(r"[\s+]+", sequence):
        key = raw.strip().lower().strip("<>")
        if not key:
            continue
        aliases = {
            "ctrl": "ctrl",
            "control": "ctrl",
            "shift": "shift",
            "alt": "alt",
            "meta": "cmd",
            "win": "cmd",
            "cmd": "cmd",
            "esc": "esc",
            "space": "space",
        }
        key = aliases.get(key, key)
        if len(key) == 1:
            parts_out.append(key)
        else:
            parts_out.append(f"<{key}>")
    return "+".join(parts_out)


class HotkeyManager(QObject):
    """注册全局快捷键，把触发事件转发到主线程信号。"""

    triggered = Signal(str)  # action 名称

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._listener = None
        self._registry: dict[str, str] = {}
        self._enabled = True

    @property
    def enabled(self) -> bool:
        return self._enabled

    def apply(self, hotkeys: dict[str, str]) -> None:
        """hotkeys: {action: shortcut}，如 {'capture_region': 'ctrl+shift+a'}。"""
        self.stop()
        self._registry = dict(hotkeys)

        # 冲突检测：同一组合绑定多个动作
        seen: dict[str, str] = {}
        conflicts: list[str] = []
        for action, shortcut in self._registry.items():
            combo = normalize_hotkey(shortcut)
            if not combo:
                continue
            if combo in seen and seen[combo] != action:
                conflicts.append(f"{combo}（{seen[combo]} 与 {action}）")
            else:
                seen[combo] = action

        if conflicts:
            raise HotkeyError("快捷键冲突：" + "；".join(conflicts))

        mapping: dict[str, Callable[[], None]] = {}
        for action, shortcut in self._registry.items():
            combo = normalize_hotkey(shortcut)
            if combo:
                mapping[combo] = (lambda act=action: self.triggered.emit(act))

        if not mapping:
            return

        try:
            from pynput import keyboard

            self._listener = keyboard.GlobalHotKeys(mapping)
            self._listener.daemon = True
            self._listener.start()
            log.info("已注册 %d 个全局快捷键", len(mapping))
        except Exception as exc:  # pragma: no cover - 平台相关
            self._listener = None
            raise HotkeyError(f"全局快捷键注册失败：{exc}") from exc

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
