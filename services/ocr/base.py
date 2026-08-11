"""OCR 抽象接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class OCRLine:
    text: str
    box: tuple[int, int, int, int]  # 图像内坐标 x, y, w, h
    confidence: float
    angle: float = 0.0
    block_id: int = 0


class OCRUnavailableError(RuntimeError):
    pass


class OCREngine(ABC):
    name: str = "base"

    def __init__(self, ocr_config: dict, app_config: Any = None, logger: Any = None) -> None:
        self.config = ocr_config or {}
        self.app_config = app_config
        self._logger = logger

    @classmethod
    def available(cls) -> bool:
        return True

    def warmup(self) -> None:
        """后台预热，例如下载/加载模型。"""

    @abstractmethod
    def recognize(self, image_bgr, lang: str | None = None) -> list[OCRLine]:
        """识别 BGR 图像，返回 OCRLine 列表。"""

    def close(self) -> None:
        """释放资源。"""


_REGISTRY: dict[str, type[OCREngine]] = {}


def register_engine(engine_class: type[OCREngine]) -> None:
    _REGISTRY[engine_class.name] = engine_class


def list_ocr_engines() -> list[str]:
    return list(_REGISTRY.keys())


def create_ocr_engine(name: str, ocr_config: dict, app_config: Any = None) -> OCREngine:
    engine_class = _REGISTRY.get(name)
    if engine_class is None:
        raise OCRUnavailableError(f"未知 OCR 引擎：{name}")
    if not engine_class.available():
        raise OCRUnavailableError(f"OCR 引擎 {name} 不可用（依赖未安装或初始化失败）")
    return engine_class(ocr_config, app_config)
