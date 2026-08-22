"""Entrypoint for the isolated PaddleOCR onedir component.

It is deliberately not imported by the lightweight application.  The frozen
component accepts a PNG job and writes a small, stable JSON contract.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any


def component_models_directory() -> Path:
    """Return the component-owned, writable PaddleX model/cache directory."""
    # PyInstaller onedir stores collected data under ``_internal``.  In source
    # runs keep the same contract beside this worker.  This is set before any
    # Paddle/PaddleX import, otherwise PaddleX may cache into the light app.
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    models = base / "models"
    models.mkdir(parents=True, exist_ok=True)
    return models


def configure_paddlex_cache() -> Path:
    models = component_models_directory()
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(models)
    return models


def _box(poly: Any) -> list[int]:
    try:
        xs = [float(point[0]) for point in poly]
        ys = [float(point[1]) for point in poly]
        x0, y0, x1, y1 = round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))
        return [x0, y0, max(1, x1 - x0), max(1, y1 - y0)]
    except Exception:
        return [0, 0, 0, 0]


def _lines(result: Any) -> list[dict[str, Any]]:
    result = [result] if isinstance(result, dict) else (result or [])
    lines: list[dict[str, Any]] = []
    for item in result:
        read = item.get if isinstance(item, dict) else lambda key: getattr(item, key, None)
        texts = read("rec_texts") or read("texts") or []
        scores = read("rec_scores") or read("scores") or []
        polys = read("rec_polys") or read("dt_polys") or read("polys") or []
        angles = read("rec_angles") or []
        for index, value in enumerate(texts):
            text = str(value).strip()
            if text:
                lines.append({
                    "text": text,
                    "box": _box(polys[index]) if index < len(polys) else [0, 0, 0, 0],
                    "confidence": float(scores[index]) if index < len(scores) else 1.0,
                    "angle": float(angles[index]) if index < len(angles) else 0.0,
                    "block_id": 0,
                })
    return lines


class Worker:
    def __init__(self) -> None:
        configure_paddlex_cache()
        self._engines: dict[str, Any] = {}

    def engine(self, lang: str) -> Any:
        if lang not in self._engines:
            from paddleocr import PaddleOCR
            self._engines[lang] = PaddleOCR(
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=True,
                text_detection_model_name="PP-OCRv5_mobile_det",
                text_recognition_model_name="PP-OCRv5_mobile_rec",
                lang=lang,
            )
        return self._engines[lang]

    def recognize(self, input_path: Path, lang: str) -> list[dict[str, Any]]:
        import cv2
        image = cv2.imread(str(input_path))
        if image is None:
            raise ValueError("无法读取输入 PNG")
        engine = self.engine(lang)
        try:
            return _lines(engine.predict(image))
        except AttributeError:
            # Kept for a component accidentally built against PaddleOCR 2.x.
            legacy = engine.ocr(image, cls=True)
            lines: list[dict[str, Any]] = []
            for block in (legacy[0] if legacy else []):
                polygon, (text, confidence) = block
                lines.append({"text": str(text), "box": _box(polygon), "confidence": float(confidence), "angle": 0.0, "block_id": 0})
            return lines

    def warmup(self, lang: str) -> None:
        import numpy as np
        self.engine(lang).predict(np.zeros((64, 64, 3), dtype=np.uint8))


def _write_output(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path)
    parser.add_argument("--lang", default="ch")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--warmup", action="store_true")
    args = parser.parse_args(argv)
    if not args.warmup and (args.input is None or args.output is None):
        parser.error("--input 和 --output 是必需参数")
    try:
        worker = Worker()
        if args.warmup:
            worker.warmup(args.lang)
            return 0
        _write_output(args.output, {"lines": worker.recognize(args.input, args.lang)})
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
