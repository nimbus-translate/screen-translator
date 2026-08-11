"""Windows.Media.Ocr 兜底引擎（winocr 包，离线可用）。"""

from __future__ import annotations

import threading

from app.logger import get_logger
from services.ocr.base import OCRLine, OCREngine, register_engine
from utils.language_utils import to_windows_lang

log = get_logger("ocr.windows")


class WindowsOCREngine(OCREngine):
    name = "windows"

    def __init__(self, ocr_config: dict, app_config=None, logger=None) -> None:
        super().__init__(ocr_config, app_config, logger)
        self._lock = threading.Lock()

    @classmethod
    def available(cls) -> bool:
        try:
            import winocr  # noqa: F401

            return True
        except Exception:
            return False

    def recognize(self, image_bgr, lang: str | None = None) -> list[OCRLine]:
        import cv2
        import winocr
        from PIL import Image

        lang = lang or str(self.config.get("lang", "ch"))
        tags = [to_windows_lang(lang)]
        if "zh" in lang or lang == "auto":
            tags = ["zh-Hans-CN", "zh-Hant-TW", "en-US"]
        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(rgb)

        last_error: Exception | None = None
        for tag in tags:
            try:
                with self._lock:
                    result = winocr.recognize_pil(pil, lang=tag)
                return self._to_lines(result)
            except Exception as exc:  # 语言包缺失等
                last_error = exc
        raise RuntimeError(f"Windows OCR 不可用（需要安装对应语言包）：{last_error}")

    @staticmethod
    def _to_lines(result) -> list[OCRLine]:
        lines: list[OCRLine] = []
        for line in getattr(result, "lines", []) or []:
            words = getattr(line, "words", None) or []
            if not words:
                text = str(getattr(line, "text", "") or "").strip()
                if text:
                    lines.append(OCRLine(text=text, box=(0, 0, 0, 0), confidence=1.0))
                continue
            xs, ys, x2s, y2s = [], [], [], []
            text_parts = []
            for word in words:
                rect = getattr(word, "bounding_rect", None)
                if rect is not None:
                    xs.append(int(rect.x))
                    ys.append(int(rect.y))
                    x2s.append(int(rect.x + rect.width))
                    y2s.append(int(rect.y + rect.height))
                text_parts.append(str(getattr(word, "text", "") or ""))
            if not xs:
                continue
            text = " ".join(part for part in text_parts if part).strip()
            if not text:
                continue
            lines.append(
                OCRLine(
                    text=text,
                    box=(min(xs), min(ys), max(x2s) - min(xs), max(y2s) - min(ys)),
                    confidence=1.0,
                )
            )
        return lines


register_engine(WindowsOCREngine)
