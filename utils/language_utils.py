"""语言代码与显示名、各服务语言参数映射。"""

from __future__ import annotations

import re

LANGUAGES = [
    ("auto", "自动检测"),
    ("zh", "简体中文"),
    ("zh-hant", "繁体中文"),
    ("en", "英语"),
    ("ja", "日语"),
    ("ko", "韩语"),
    ("fr", "法语"),
    ("de", "德语"),
    ("es", "西班牙语"),
    ("ru", "俄语"),
    ("pt", "葡萄牙语"),
    ("it", "意大利语"),
    ("vi", "越南语"),
    ("th", "泰语"),
    ("ar", "阿拉伯语"),
]

LANGUAGE_CODES = [code for code, _ in LANGUAGES]


_TECHNICAL_PATH_RE = re.compile(r"^[\w.@:+~-]+(?:[/\\][\w.@:+~-]+)+$")
_TECHNICAL_FILE_RE = re.compile(
    r"^[\w@+-]+\.(?:py|pyw|md|json|toml|ini|cfg|conf|spec|txt|ya?ml|"
    r"exe|dll|so|dylib|zip|7z|rar|png|jpe?g|gif|svg|html?|css|js|ts)$",
    re.IGNORECASE,
)
_HEX_ID_RE = re.compile(r"^[0-9a-f]{7,64}$", re.IGNORECASE)


def needs_translation(text: str, target_language: str) -> bool:
    """Reject UI glyphs, technical identifiers and text already in the target script."""

    value = (text or "").strip()
    if not value:
        return False
    letters = [char for char in value if char.isalpha()]
    if not letters or (len(letters) == 1 and letters[0].isascii()):
        return False
    if _TECHNICAL_PATH_RE.fullmatch(value) or _TECHNICAL_FILE_RE.fullmatch(value):
        return False
    if _HEX_ID_RE.fullmatch(value):
        return False

    target = (target_language or "").lower()
    if target == "zh" and any("\u3400" <= char <= "\u9fff" for char in value):
        return False
    if target.startswith("ja") and any(
        "\u3040" <= char <= "\u30ff" for char in value
    ):
        return False
    if target.startswith("ko") and any(
        "\uac00" <= char <= "\ud7af" for char in value
    ):
        return False
    if target.startswith("ru") and any(
        "\u0400" <= char <= "\u04ff" for char in value
    ):
        return False
    if target.startswith("ar") and any(
        "\u0600" <= char <= "\u06ff" for char in value
    ):
        return False
    if target.startswith("th") and any(
        "\u0e00" <= char <= "\u0e7f" for char in value
    ):
        return False
    return True


def display_name(code: str) -> str:
    for c, name in LANGUAGES:
        if c == code:
            return name
    return code


def to_paddle_lang(code: str) -> str:
    return {
        "auto": "ch",
        "zh": "ch",
        "zh-hant": "chinese_cht",
        "en": "en",
        "ja": "japan",
        "ko": "korean",
        "fr": "french",
        "de": "german",
        "es": "spanish",
        "ru": "russian",
        "pt": "portuguese",
        "it": "italian",
        "vi": "vietnam",
        "th": "thai",
        "ar": "arabic",
    }.get(code, "ch")


def to_openai_lang(code: str) -> str:
    return {
        "auto": "auto",
        "zh": "简体中文 (zh)",
        "zh-hant": "繁体中文 (zh-Hant)",
        "en": "English (en)",
        "ja": "日本語 (ja)",
        "ko": "한국어 (ko)",
        "fr": "français (fr)",
        "de": "Deutsch (de)",
        "es": "español (es)",
        "ru": "русский (ru)",
        "pt": "português (pt)",
        "it": "italiano (it)",
        "vi": "tiếng Việt (vi)",
        "th": "ไทย (th)",
        "ar": "العربية (ar)",
    }.get(code, code)


def to_deepl_lang(code: str) -> str:
    return {
        "auto": "",
        "zh": "ZH",
        "zh-hant": "ZH",
        "en": "EN-US",
        "ja": "JA",
        "ko": "KO",
        "fr": "FR",
        "de": "DE",
        "es": "ES",
        "ru": "RU",
        "pt": "PT-BR",
        "it": "IT",
        "vi": "VI",
        "th": "TH",
        "ar": "AR",
    }.get(code, code.upper())


def to_google_lang(code: str) -> str:
    return {
        "auto": "",
        "zh": "zh-CN",
        "zh-hant": "zh-TW",
    }.get(code, code)


def to_windows_lang(code: str) -> str:
    return {
        "auto": "zh-Hans-CN",
        "zh": "zh-Hans-CN",
        "zh-hant": "zh-Hant-TW",
        "en": "en-US",
        "ja": "ja-JP",
        "ko": "ko-KR",
        "fr": "fr-FR",
        "de": "de-DE",
        "es": "es-ES",
        "ru": "ru-RU",
        "pt": "pt-BR",
        "it": "it-IT",
        "vi": "vi-VN",
        "th": "th-TH",
        "ar": "ar-SA",
    }.get(code, "zh-Hans-CN")


def to_mymemory_lang(code: str) -> str:
    """MyMemory 使用 ISO 639-1；中文需带地区后缀。auto 默认按英文处理。"""
    return {
        "auto": "en",
        "zh": "zh-CN",
        "zh-hant": "zh-TW",
    }.get(code, code)
