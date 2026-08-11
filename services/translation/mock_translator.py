"""Mock 翻译器：离线可测。默认带本地词典做英→中翻译，演示不再是加前缀。"""

from __future__ import annotations

import random
import re
import time

from services.translation.base import Translator, register_translator
from services.translation.mock_dictionary import PHRASES, WORDS
from utils.language_utils import display_name


class MockTranslator(Translator):
    name = "mock"

    def __init__(self, config_section: dict, cache=None, api_key: str = "") -> None:
        super().__init__(config_section, cache, api_key)
        self.unsupported_direction = False

    def _translate_batch(self, texts: list[str], source_language: str | None, target_language: str) -> list[str]:
        time.sleep(random.uniform(0.05, 0.25))
        mode = str(self.config.get("mock_mode", "dictionary"))
        if mode == "reverse":
            return [text[::-1] for text in texts]
        if target_language not in ("zh", "zh-hant") or mode != "dictionary":
            self.unsupported_direction = True
            target_name = display_name(target_language)
            return [f"[{target_name}] {text}" for text in texts]
        self.unsupported_direction = False
        return [self._translate_line(text) for text in texts]

    @staticmethod
    def _translate_line(text: str) -> str:
        lowered = text.lower().strip()
        if lowered in PHRASES:
            return PHRASES[lowered]
        # 按词替换，保留数字 / URL / 标点 / 未收录词
        parts = re.split(r"(\W+)", text)
        for idx, part in enumerate(parts):
            if re.fullmatch(r"[A-Za-z]+", part):
                translation = WORDS.get(part.lower())
                if translation:
                    parts[idx] = translation
        return "".join(parts)


register_translator(MockTranslator)
