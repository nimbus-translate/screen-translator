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
    re.compile(
        r"\bClaude\s+(?:Fable|Opus|Sonnet|Haiku|Mythos)(?:\s+\d+(?:\.\d+)?)?\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b[A-Za-z][A-Za-z0-9'-]*(?:Bench|Benchmark)(?:\s+\d+(?:\.\d+)?)?\b"),
    re.compile(
        r"\b(?:Claude|Cursor|GitHub|Anthropic|Stripe|Spotify|Bridgewater)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b\d[\d,._:/-]*%?\b"),
]

_SENTINEL = "__ST_KEEP_{:04d}__"
_SENTINEL_RE = re.compile(r"__\s*ST_KEEP_(\d+)\s*__", re.IGNORECASE)
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


def normalize_ocr_text(text: str) -> str:
    """Repair high-confidence Windows OCR confusions in tiny English UI text."""

    value = text or ""
    substitutions = (
        (r"\bSWE-8ench\b", "SWE-Bench"),
        (r"\b8enchmark\b", "Benchmark"),
        (r"\balueprint-aench\b", "Blueprint-Bench"),
        (r"\b3ioMysteryBench\b", "BioMysteryBench"),
        (r"\bVlSlOn\b", "vision"),
        (r"\bOUr\b", "our"),
        (r"\bCIaude\b", "Claude"),
        (r"\bFabIe\b", "Fable"),
        (r"\bln\b", "In"),
        (r"\btOOk\b", "took"),
        (r"\btO\b", "to"),
        (r"\bOf\b", "of"),
        (r"\b0f\b", "of"),
        (r"\bmOdelS\b", "models"),
        (r"\bexcites\s+LIS\b", "excites us"),
        (r"\blt\s*[^A-Za-z0-9\s]+\s*s\s+opened\b", "It's opened"),
        (r"\boflong\b", "of long"),
        (r"\bROd\s+rigu\s+ez\b", "Rodriguez"),
        (r"\bProd\s+uct\b", "Product"),
        (r"\bC\s+EO\b", "CEO"),
        (r"\breasonlng\b", "reasoning"),
        (r"\breason\s*[Il]\s*ng\b", "reasoning"),
        (r"\bMuItidisciplinary\b", "Multidisciplinary"),
        (r"\bl<nowledge\b", "Knowledge"),
        (r"\bFront\s*i\s*erCode\b", "FrontierCode"),
        (r"\bGDPva\s*[^A-Za-z0-9\s]?\s*AA\b", "GDPval-AA"),
        (r"\bTerminal-Bench\s+2\s*[^A-Za-z0-9\s]\s*1\b", "Terminal-Bench 2.1"),
        (r"\bGemini\s+C\s*[^A-Za-z0-9\s]+\s*$", "Gemini CLI"),
        (r"\bT00\s*[^\w\s]?\s*use\b", "Tool use"),
    )
    for pattern, replacement in substitutions:
        value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)

    # 9px 的 “tools” 是 Windows 中文 OCR 的重灾区：0/O、1/l 和小图标
    # 会随机混进来。只在 no/with 上下文修，避免误伤普通数字。
    value = re.sub(
        r"\b(no|with)\s+t[0o]{2}(?:\s*[^\w\s]\s*)?(?:[l1]?\s*[s5])?\b",
        lambda match: f"{match.group(1)} tools",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\bt[o0]{2}[l1]s\b", "tools", value, flags=re.IGNORECASE)
    value = re.sub(
        r"^(no|with)\s+tools(?:\s*[^A-Za-z0-9\s]+)?$",
        lambda match: f"{match.group(1)} tools",
        value,
        flags=re.IGNORECASE,
    )
    # Windows 中文 OCR 偶尔把英文逗号识别成一个孤立汉字（飞/卜/凵等）。
    # 仅在两侧都是拉丁词时修成逗号，避免碰真正的中文内容。
    value = re.sub(
        r"(?<=[A-Za-z])\s*(?:，|[\u3400-\u9fff])\s*(?=[A-Za-z])", ", ", value
    )
    return re.sub(r"\s{2,}", " ", value).strip()


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
    def restore(match: re.Match) -> str:
        key = _SENTINEL.format(int(match.group(1)))
        return mapping.get(key, match.group(0))

    return [_SENTINEL_RE.sub(restore, text) for text in texts]
