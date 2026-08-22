"""空 OCR：引擎不可用时的兜底，保证应用能启动。"""

from __future__ import annotations

from app.logger import get_logger
from services.ocr.base import OCRLine, OCREngine, register_engine

log = get_logger("ocr.null")


class NullOCREngine(OCREngine):
    name = "none"

    def __init__(self, ocr_config: dict, app_config=None, logger=None) -> None:
        super().__init__(ocr_config, app_config, logger)
        self._warned = False

    def recognize(self, image_bgr, lang: str | None = None) -> list[OCRLine]:
        if not self._warned:
            log.warning("当前没有可用的 OCR 引擎")
            self._warned = True
        raise RuntimeError(
            "没有可用的 OCR 引擎：请先使用 Windows OCR，或在设置 → OCR 中下载 PaddleOCR 可选组件。"
        )


register_engine(NullOCREngine)
