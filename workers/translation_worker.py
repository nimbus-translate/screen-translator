"""截图 -> OCR -> 合并 -> 翻译 -> 覆盖层数据的管线任务（QThread）。"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from app.config import AppConfig
from app.logger import get_logger
from app.models import CaptureInfo
from services.ocr.base import OCREngine
from services.translation.base import TranslationError, Translator
from utils.image_utils import (
    detect_colors,
    dominant_color,
    ensure_text_contrast,
    resize_for_ocr,
    sanitize_background,
)
from utils.layout_utils import ocr_lines_to_regions
from utils.text_utils import clean_text, protect_texts, restore_texts

log = get_logger("worker")


class PipelineTask(QThread):
    status = Signal(str)
    error = Signal(str)
    result = Signal(object)
    finished = Signal()

    def __init__(
        self,
        capture: CaptureInfo,
        ocr_engine: OCREngine,
        translator: Translator,
        config: AppConfig,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.capture = capture
        self.ocr_engine = ocr_engine
        self.translator = translator
        self.config = config
        self._stop = False

    def cancel(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            self._emit_status("正在识别...")
            # 小图先放大再 OCR：游戏截图/弹窗内的小字识别率提升数倍
            image, scale = resize_for_ocr(self.capture.image)
            lines = self.ocr_engine.recognize(
                image, lang=str(self.config.get("ocr.lang", "ch"))
            )
            if self._stop:
                return
            for line in lines:
                line.text = clean_text(line.text)
            if scale != 1.0:
                for line in lines:
                    x, y, w, h = line.box
                    line.box = (
                        int(round(x / scale)),
                        int(round(y / scale)),
                        int(round(w / scale)),
                        int(round(h / scale)),
                    )
            if not lines:
                self.error.emit("没有识别到文字（检查 OCR 引擎设置或置信度阈值）")
                return

            regions = ocr_lines_to_regions(
                lines,
                offset_x=self.capture.offset_x,
                offset_y=self.capture.offset_y,
                min_confidence=float(self.config.get("ocr.min_confidence", 0.6)),
                y_tolerance_ratio=float(self.config.get("ocr.merge_y_tolerance_ratio", 0.5)),
                x_gap_ratio=float(self.config.get("ocr.merge_x_gap_ratio", 0.8)),
            )
            if not regions:
                self.error.emit("没有识别到文字（置信度过滤后为空）")
                return

            # 背景亮度 -> 自动文字颜色
            auto_color = bool(self.config.get("overlay.use_auto_text_color", True))
            default_color = str(self.config.get("overlay.text_color", "#FFFFFF"))
            # 整图页面基调色，用于交叉校验块级背景识别
            global_background = dominant_color(self.capture.image)
            for region in regions:
                background_hex, text_hex, lum = detect_colors(
                    self.capture.image,
                    region.x - self.capture.offset_x,
                    region.y - self.capture.offset_y,
                    region.width,
                    region.height,
                )
                region.source_luminance = lum
                region.background_color = sanitize_background(background_hex, global_background)
                region.text_color = text_hex if auto_color else default_color
                if auto_color:
                    region.text_color = ensure_text_contrast(region.text_color, region.background_color)

            self._emit_status(f"正在翻译 {len(regions)} 个文本块...")
            source = str(self.config.get("translation.source_language", "auto"))
            target = str(self.config.get("translation.target_language", "zh"))
            texts = [region.text for region in regions]
            # 同批去重：重复文本只请求一次，减少免费服务限流压力
            unique_texts = list(dict.fromkeys(texts))
            protected, mapping = protect_texts(unique_texts)
            translated = self.translator.translate(protected, source, target)
            restored_unique = [clean_text(text) for text in restore_texts(translated, mapping)]
            restored_map = dict(zip(unique_texts, restored_unique))
            restored = [restored_map[text] for text in texts]

            if self.config.get("translation.keep_original", False):
                restored = [f"{original}\n{translated_text}" for original, translated_text in zip(texts, restored)]

            if self._stop:
                return

            for region, translated_text in zip(regions, restored):
                region.translated_text = translated_text

            failed = getattr(self.translator, "last_failed_count", 0)
            if failed:
                self._emit_status(f"翻译完成：{failed} 个文本块失败，已保留原文")
            if getattr(self.translator, "unsupported_direction", False):
                self._emit_status(
                    "Mock 演示翻译只支持输出中文，当前目标语言下仅保留原文；"
                    "请在 设置 → 翻译 中填写 OpenAI/DeepL/Google API Key 使用真实翻译"
                )

            self._emit_status("正在生成覆盖层...")
            self.result.emit({"capture": self.capture, "regions": regions, "failed_count": failed})
        except TranslationError as exc:
            self.error.emit(str(exc))
        except Exception as exc:
            log.exception("管线处理失败")
            self.error.emit(f"处理失败：{exc}")
        finally:
            self.finished.emit()

    def _emit_status(self, text: str) -> None:
        self.status.emit(text)
