"""核心数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np


@dataclass
class TextRegion:
    """一个待翻译 / 已翻译的文本块。

    x/y/width/height 一律使用物理像素（全局虚拟桌面坐标），与 mss 截图坐标一致。
    """

    text: str
    translated_text: str = ""
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    confidence: float = 1.0
    screen_index: int = 0
    source_luminance: float = 0.5
    text_color: str = "#FFFFFF"
    background_color: str = ""

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)

    def with_translation(self, translated: str) -> "TextRegion":
        return replace(self, translated_text=translated)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "translated_text": self.translated_text,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "confidence": self.confidence,
            "screen_index": self.screen_index,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TextRegion":
        return cls(
            text=data.get("text", ""),
            translated_text=data.get("translated_text", ""),
            x=int(data.get("x", 0)),
            y=int(data.get("y", 0)),
            width=int(data.get("width", 0)),
            height=int(data.get("height", 0)),
            confidence=float(data.get("confidence", 1.0)),
            screen_index=int(data.get("screen_index", 0)),
        )


@dataclass
class CaptureInfo:
    """一次截图的结果。

    bbox 为物理像素全局坐标 (left, top, right, bottom)。
    """

    image: np.ndarray  # BGR
    bbox: tuple[int, int, int, int]
    monitor_indices: list[int] = field(default_factory=list)
    mode: str = "region"

    @property
    def offset_x(self) -> int:
        return self.bbox[0]

    @property
    def offset_y(self) -> int:
        return self.bbox[1]

    def region_rect(self, index: int) -> dict[str, int]:
        """截图内第 index 个 OCR 框的物理全局坐标。"""
        return {"x": self.bbox[0], "y": self.bbox[1]}
