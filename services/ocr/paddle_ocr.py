"""PaddleOCR 3.x 适配器（兼容 2.x 旧 API）。"""

from __future__ import annotations

import threading
from typing import Any

import numpy as np

from app.logger import get_logger
from services.ocr.base import OCRLine, OCREngine, register_engine
from utils.language_utils import to_paddle_lang

log = get_logger("ocr.paddle")


def _poly_to_box(poly) -> tuple[int, int, int, int]:
    if poly is None:
        return (0, 0, 0, 0)
    try:
        arr = np.asarray(poly, dtype=np.float32)
        if arr.ndim == 3:
            arr = arr[0]
        if arr.size == 0:
            return (0, 0, 0, 0)
        xs = arr[:, 0]
        ys = arr[:, 1]
        x0, y0 = int(round(xs.min())), int(round(ys.min()))
        x1, y1 = int(round(xs.max())), int(round(ys.max()))
        return (x0, y0, max(1, x1 - x0), max(1, y1 - y0))
    except Exception:
        return (0, 0, 0, 0)


def _normalize_result(result) -> list[OCRLine]:
    if result is None:
        return []
    if isinstance(result, dict):
        result = [result]
    elif not isinstance(result, (list, tuple)):
        result = [result]

    lines: list[OCRLine] = []
    for item in result:
        if item is None:
            continue
        if isinstance(item, dict):
            texts = item.get("rec_texts") or item.get("texts") or []
            scores = item.get("rec_scores") or item.get("scores") or []
            polys = item.get("rec_polys") or item.get("dt_polys") or item.get("polys") or []
            angles = item.get("rec_angles") or []
        else:
            texts = getattr(item, "rec_texts", None) or getattr(item, "texts", None) or []
            scores = getattr(item, "rec_scores", None) or getattr(item, "scores", None) or []
            polys = getattr(item, "rec_polys", None) or getattr(item, "dt_polys", None) or getattr(item, "polys", None) or []
            angles = getattr(item, "rec_angles", None) or []

        count = len(texts)
        for i in range(count):
            text = str(texts[i]).strip()
            if not text:
                continue
            score = float(scores[i]) if i < len(scores) else 1.0
            poly = polys[i] if i < len(polys) else None
            angle = float(angles[i]) if i < len(angles) else 0.0
            lines.append(OCRLine(text=text, box=_poly_to_box(poly), confidence=score, angle=angle))
    return lines


class PaddleOCREngine(OCREngine):
    name = "paddle"

    def __init__(self, ocr_config: dict, app_config: Any = None, logger: Any = None) -> None:
        super().__init__(ocr_config, app_config, logger)
        self._engine: Any = None
        self._lang: str = ""
        self._lock = threading.Lock()
        self._fail_reason: str | None = None

    @classmethod
    def available(cls) -> bool:
        try:
            import paddleocr  # noqa: F401
            import paddle  # noqa: F401

            return True
        except Exception:
            log.exception("PaddleOCR 依赖检查失败")
            return False

    def _build_engine(self, lang: str) -> None:
        from paddleocr import PaddleOCR

        kwargs: dict[str, Any] = {
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": bool(
                self.config.get("paddle", {}).get("use_textline_orientation", True)
            ),
            "text_detection_model_name": str(
                self.config.get("paddle", {}).get("text_detection_model_name", "PP-OCRv5_mobile_det")
            ),
            "text_recognition_model_name": str(
                self.config.get("paddle", {}).get("text_recognition_model_name", "PP-OCRv5_mobile_rec")
            ),
            "lang": to_paddle_lang(lang),
        }
        try:
            if self.config.get("paddle", {}).get("use_gpu", False):
                kwargs["device"] = "gpu"
        except Exception:
            pass
        try:
            self._engine = PaddleOCR(**kwargs)
        except TypeError:
            # 老版本不认识的参数去掉再试
            kwargs.pop("use_doc_orientation_classify", None)
            kwargs.pop("use_doc_unwarping", None)
            kwargs.pop("device", None)
            self._engine = PaddleOCR(**kwargs)
        self._lang = lang

    def warmup(self) -> None:
        lang = str(self.config.get("lang", "ch"))
        with self._lock:
            if self._engine is None:
                self._build_engine(lang)
            try:
                self._engine.predict(np.zeros((64, 64, 3), dtype=np.uint8))
            except Exception as exc:
                self._fail_reason = str(exc)
                log.warning("PaddleOCR 预热失败（首次使用时会重试）：%s", exc)

    def recognize(self, image_bgr, lang: str | None = None) -> list[OCRLine]:
        lang = lang or str(self.config.get("lang", "ch"))
        with self._lock:
            if self._engine is None or lang != self._lang:
                try:
                    self._build_engine(lang)
                except Exception as exc:
                    self._fail_reason = str(exc)
                    raise RuntimeError(f"PaddleOCR 初始化失败：{exc}") from exc
            try:
                result = self._engine.predict(image_bgr)
                return _normalize_result(result)
            except AttributeError:
                # 兼容 PaddleOCR 2.x 的 .ocr() 接口
                legacy = self._engine.ocr(image_bgr, cls=True)
                lines: list[OCRLine] = []
                if legacy:
                    for block in legacy[0]:
                        box = block[0]
                        text, score = block[1]
                        xs = [p[0] for p in box]
                        ys = [p[1] for p in box]
                        lines.append(
                            OCRLine(
                                text=str(text),
                                box=(int(min(xs)), int(min(ys)), int(max(xs) - min(xs)), int(max(ys) - min(ys))),
                                confidence=float(score),
                            )
                        )
                return lines
            except Exception as exc:
                raise RuntimeError(f"PaddleOCR 识别失败：{exc}") from exc


register_engine(PaddleOCREngine)
