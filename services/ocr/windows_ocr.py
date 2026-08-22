"""Optional Windows 10/11 ``Windows.Media.Ocr`` adapter.

The adapter imports ``winocr`` lazily.  This keeps the normal installation
small and lets non-Windows test runs import the module without WinRT bindings.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import re
import sys
import threading
from collections.abc import Mapping
from typing import Any

import numpy as np
from PIL import Image

from services.ocr.base import OCRLine, OCREngine, register_engine
from utils.language_utils import to_windows_lang


_WINOCR_INSTALL_HINT = "请安装可选依赖：pip install winocr"


def _field(value: Any, name: str, default: Any = None) -> Any:
    """Read a field from both WinRT objects and winocr sync dictionaries."""

    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


class WindowsOCREngine(OCREngine):
    """Offline OCR backed by Windows.Media.Ocr through optional ``winocr``."""

    name = "windows"

    def __init__(self, ocr_config: dict, app_config=None, logger=None) -> None:
        super().__init__(ocr_config, app_config, logger)
        self._lock = threading.Lock()
        self.last_language_tag = ""

    @classmethod
    def availability_reason(cls) -> str | None:
        """Return an actionable reason instead of an import traceback."""

        if sys.platform != "win32":
            return "Windows OCR 仅支持 Windows 10/11"
        version = getattr(sys, "getwindowsversion", None)
        if version is not None and version().major < 10:
            return "Windows OCR 需要 Windows 10 或更高版本"
        try:
            module = importlib.import_module("winocr")
        except Exception as exc:
            return f"Windows OCR 依赖不可用（{exc.__class__.__name__}）：{_WINOCR_INSTALL_HINT}"
        if not callable(getattr(module, "recognize_pil", None)) and not callable(
            getattr(module, "recognize_pil_sync", None)
        ):
            return f"winocr 安装不完整（缺少 recognize_pil）：{_WINOCR_INSTALL_HINT}"
        return None

    @classmethod
    def available(cls) -> bool:
        return cls.availability_reason() is None

    @staticmethod
    def _load_winocr():
        try:
            return importlib.import_module("winocr")
        except Exception as exc:
            raise RuntimeError(
                f"Windows OCR 依赖不可用：{_WINOCR_INSTALL_HINT}（{exc.__class__.__name__}）"
            ) from exc

    @staticmethod
    def _to_pil_rgb(image_bgr: Any) -> Image.Image:
        """Validate an OpenCV-style BGR ndarray and convert it without cv2."""

        if not isinstance(image_bgr, np.ndarray):
            raise TypeError("Windows OCR 需要 numpy BGR 图像")
        if image_bgr.ndim != 3 or image_bgr.shape[2] not in (3, 4):
            raise ValueError("Windows OCR 需要 H×W×3 或 H×W×4 的 BGR 图像")
        if image_bgr.size == 0:
            raise ValueError("Windows OCR 不能识别空图像")
        if image_bgr.dtype != np.uint8:
            raise ValueError("Windows OCR 需要 uint8 BGR 图像")

        rgb = np.ascontiguousarray(image_bgr[:, :, :3][:, :, ::-1])
        return Image.fromarray(rgb, mode="RGB")

    @staticmethod
    def _wait_for_result(result: Any) -> Any:
        if not inspect.isawaitable(result):
            return result
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("Windows OCR 不能在运行中的 asyncio 事件循环内同步执行")

        async def wait() -> Any:
            return await result

        return asyncio.run(wait())

    @classmethod
    def _recognize(cls, winocr: Any, image: Image.Image, language: str) -> Any:
        sync_recognize = getattr(winocr, "recognize_pil_sync", None)
        if callable(sync_recognize):
            return sync_recognize(image, language)
        recognize = getattr(winocr, "recognize_pil", None)
        if not callable(recognize):
            raise RuntimeError(f"winocr 安装不完整（缺少 recognize_pil）：{_WINOCR_INSTALL_HINT}")
        return cls._wait_for_result(recognize(image, language))

    @staticmethod
    def _installed_language_tags(winocr: Any) -> list[str]:
        """Read WinRT OCR language packs without depending on a winocr API."""

        engine_type = getattr(winocr, "OcrEngine", None)
        try:
            languages = getattr(engine_type, "available_recognizer_languages", None)
        except Exception:
            return []
        tags: list[str] = []
        for item in languages or []:
            tag = str(_field(item, "language_tag", "") or "").strip()
            if tag and tag.casefold() not in {value.casefold() for value in tags}:
                tags.append(tag)
        return tags

    @classmethod
    def _language_tags(cls, language: str, winocr: Any | None = None) -> list[str]:
        if language != "auto":
            return [to_windows_lang(language)]

        installed = cls._installed_language_tags(winocr) if winocr is not None else []
        if not installed:
            # Preserve the dependency-agnostic fallback used by tests and by
            # older winocr builds that do not expose OcrEngine.
            return ["zh-Hans-CN", "en-US"]

        preferred = ["en-US", "zh-Hans-CN", "ja-JP"]
        by_folded = {tag.casefold(): tag for tag in installed}
        ordered = [by_folded[tag.casefold()] for tag in preferred if tag.casefold() in by_folded]
        ordered.extend(tag for tag in installed if tag not in ordered)
        # Running every installed language pack can turn one capture into a
        # multi-second stall. Six covers the common multilingual setups.
        return ordered[:6]

    @staticmethod
    def _quality_score(lines: list[OCRLine], language_tag: str) -> float:
        """Prefer complete, low-noise text when auto mode has several results."""

        text = "\n".join(line.text for line in lines).strip()
        if not text:
            return float("-inf")
        visible = [char for char in text if not char.isspace()]
        letters = [char for char in text if char.isalpha()]
        latin = [char for char in letters if char.isascii()]
        non_latin = len(letters) - len(latin)
        score = float(len(visible) + len(latin) * 0.35)
        score += sum(3.0 for line in lines if line.box[2] > 0 and line.box[3] > 0)
        score -= text.count("\ufffd") * 18.0
        score -= sum(
            10.0
            for line in lines
            if len([char for char in line.text if char.isalnum()]) <= 1
        )
        ordered_y = [
            line.box[1]
            for line in lines
            if line.box[2] > 0
            and line.box[3] > 0
            and len([char for char in line.text if char.isalnum()]) >= 4
        ]
        # Horizontal Windows OCR normally emits top-to-bottom. A recognizer
        # that mistakes the page for vertical text often returns the paragraph
        # in reverse order and inserts stray arrow/glyph lines.
        score -= sum(
            14.0
            for previous, current in zip(ordered_y, ordered_y[1:])
            if current + 4 < previous
        )

        # A Latin paragraph recognized through an Asian pack often contains
        # isolated bars/brackets/CJK glyphs where the pronoun "I" should be.
        if len(latin) >= max(12, non_latin * 3):
            score -= non_latin * 4.0
            score -= len(re.findall(r"(?<!\w)[|｜\[\]《》丨ー](?!\w)", text)) * 6.0
            if language_tag.casefold().startswith("en"):
                score += 24.0
        return score

    def recognize(self, image_bgr, lang: str | None = None) -> list[OCRLine]:
        image = self._to_pil_rgb(image_bgr)
        winocr = self._load_winocr()
        language = lang or str(self.config.get("lang", "auto"))
        errors: list[str] = []
        candidates: list[tuple[float, str, list[OCRLine]]] = []
        successful_attempt = False

        for tag in self._language_tags(language, winocr):
            try:
                with self._lock:
                    result = self._recognize(winocr, image, tag)
                successful_attempt = True
                lines = self._to_lines(result)
                if language != "auto":
                    self.last_language_tag = tag
                    return lines
                if lines:
                    candidates.append((self._quality_score(lines, tag), tag, lines))
            except Exception as exc:
                errors.append(f"{tag}: {exc}")

        if candidates:
            _score, tag, lines = max(candidates, key=lambda item: item[0])
            self.last_language_tag = tag
            return lines
        if successful_attempt:
            self.last_language_tag = ""
            return []

        raise RuntimeError(
            "Windows OCR 识别失败。请在 Windows 设置中安装对应的 OCR 语言功能包"
            f"（{'; '.join(errors)}）"
        )

    @staticmethod
    def _to_lines(result: Any) -> list[OCRLine]:
        lines: list[OCRLine] = []
        for line in _field(result, "lines", []) or []:
            words = _field(line, "words", []) or []
            text_parts: list[str] = []
            lefts: list[int] = []
            tops: list[int] = []
            rights: list[int] = []
            bottoms: list[int] = []
            for word in words:
                word_text = str(_field(word, "text", "") or "").strip()
                if word_text:
                    text_parts.append(word_text)
                rect = _field(word, "bounding_rect")
                if rect is None:
                    continue
                try:
                    x = int(_field(rect, "x"))
                    y = int(_field(rect, "y"))
                    width = int(_field(rect, "width"))
                    height = int(_field(rect, "height"))
                except (TypeError, ValueError):
                    continue
                if width > 0 and height > 0:
                    lefts.append(x)
                    tops.append(y)
                    rights.append(x + width)
                    bottoms.append(y + height)

            text = " ".join(text_parts).strip() or str(_field(line, "text", "") or "").strip()
            if not text:
                continue
            box = (0, 0, 0, 0)
            if lefts:
                box = (min(lefts), min(tops), max(rights) - min(lefts), max(bottoms) - min(tops))
            lines.append(OCRLine(text=text, box=box, confidence=1.0))
        return lines


register_engine(WindowsOCREngine)
