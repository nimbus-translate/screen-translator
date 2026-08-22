"""DeepL API 翻译适配器。"""

from __future__ import annotations

import requests

from services.translation.base import TranslationError, Translator, register_translator
from utils.language_utils import to_deepl_lang


class DeepLTranslator(Translator):
    name = "deepl"

    def __init__(self, config_section: dict, cache=None, api_key: str = "") -> None:
        super().__init__(config_section, cache)
        deepl_cfg = config_section.get("deepl", {}) if config_section else {}
        self.api_key = api_key or str(deepl_cfg.get("api_key", ""))
        self.base_url = str(deepl_cfg.get("base_url", "https://api-free.deepl.com/v2")).rstrip("/")

    def _translate_batch(self, texts: list[str], source_language: str | None, target_language: str) -> list[str]:
        if not self.api_key:
            raise TranslationError("缺少 DeepL API Key：请设置 DEEPL_API_KEY 或在设置中填写")
        target = to_deepl_lang(target_language)
        if not target:
            raise TranslationError("DeepL 不支持自动目标语言，请选择具体语言")
        params = {
            "auth_key": self.api_key,
            "target_lang": target,
            "text": texts,
        }
        source = to_deepl_lang(source_language) if source_language and source_language != "auto" else None
        if source:
            params["source_lang"] = source
        try:
            response = requests.post(
                f"{self.base_url}/translate",
                data=params,
                timeout=float(self.config.get("timeout_seconds", 30)),
            )
        except requests.RequestException as exc:
            raise TranslationError("DeepL 请求失败（网络连接异常）") from exc
        response.encoding = "utf-8"
        if response.status_code == 403:
            raise TranslationError("DeepL API Key 无效")
        if response.status_code == 456:
            raise TranslationError("DeepL 翻译额度不足（456）")
        if response.status_code != 200:
            raise TranslationError(f"DeepL 返回错误 {response.status_code}")
        try:
            translations = response.json()["translations"]
            return [item["text"] for item in translations]
        except (KeyError, ValueError) as exc:
            raise TranslationError("DeepL 响应格式异常") from exc


register_translator(DeepLTranslator)
