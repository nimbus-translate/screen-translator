"""翻译前保护数字 / URL / 变量占位符，翻译后还原。"""

from __future__ import annotations

import re

_PROTECT_PATTERNS = [
    re.compile(r"https?://[^\s]+"),
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    re.compile(r"\$?\{[^{}]+\}"),
    re.compile(r"%[a-zA-Z][\w.-]*%?"),
    re.compile(r"\b0x[0-9a-fA-F]+\b"),
    re.compile(r"#[0-9a-fA-F]{3,8}\b"),
    re.compile(r"\b\d[\d,._:/-]*%?\b"),
]

_SENTINEL = "\x02{}\x03"
_SENTINEL_RE = re.compile(r"\x02(\d+)\x03")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")


def clean_text(text: str) -> str:
    """去掉 OCR / 网络文本里常见的不可见与控制字符，避免显示成乱码或方块。"""
    if not text:
        return text
    text = _CONTROL_CHARS_RE.sub("", text)
    text = _ZERO_WIDTH_RE.sub("", text)
    text = text.replace("\ufffd", "?")
    return text.strip()


def protect_texts(texts: list[str]) -> tuple[list[str], dict[str, str]]:
    """把需要原样保留的 token 替换成哨兵，返回 (保护后的文本, 哨兵->原文)。"""
    mapping: dict[str, str] = {}
    protected: list[str] = []
    counter = [0]
    combined = re.compile("|".join(f"(?:{pattern.pattern})" for pattern in _PROTECT_PATTERNS))

    def _replace(match: re.Match) -> str:
        token = match.group(0)
        placeholder = _SENTINEL.format(counter[0])
        mapping[placeholder] = token
        counter[0] += 1
        return placeholder

    for text in texts:
        protected.append(combined.sub(_replace, text))
    return protected, mapping


def restore_texts(texts: list[str], mapping: dict[str, str]) -> list[str]:
    return [_SENTINEL_RE.sub(lambda m: mapping.get(m.group(0), m.group(0)), text) for text in texts]
