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
            "没有可用的 OCR 引擎：请在设置 → OCR 中选择 paddle（推荐，首次使用需联网下载模型）"
            "或 windows（需要系统已安装 OCR 语言包）"
        )


register_engine(NullOCREngine)
