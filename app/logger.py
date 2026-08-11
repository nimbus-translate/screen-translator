"""日志模块：文件 + 控制台，自动脱敏 API Key。"""

from __future__ import annotations

import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_KEY_VALUE_RE = re.compile(
    r"(?i)((?:api[_-]?key|apikey|secret|token|authorization|password)\s*[:=]\s*)(['\"]?)([^'\",\s]{6,})"
)
_SK_RE = re.compile(r"(?i)\bsk-[a-z0-9_\-]{16,}")
_BEARER_RE = re.compile(r"(?i)\b(bearer)\s+[a-z0-9._\-]{6,}")


def redact(text: str) -> str:
    """把常见的密钥形态替换成 ***，避免泄露到日志。"""
    if not text:
        return text
    text = _KEY_VALUE_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
    text = _SK_RE.sub("sk-***", text)
    text = _BEARER_RE.sub(r"\1 ***", text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        return redact(msg)


def app_data_dir() -> Path:
    override = os.environ.get("SCREEN_TRANSLATOR_CONFIG")
    if override:
        return Path(override).parent
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "ScreenTranslator"


def log_dir() -> Path:
    return app_data_dir() / "logs"


def setup_logging(level: int = logging.INFO, directory: Path | None = None) -> logging.Logger:
    directory = directory or log_dir()
    directory.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("screen_translator")
    if root.handlers:
        return root
    root.setLevel(level)

    formatter = RedactingFormatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        directory / "app.log", maxBytes=1_048_576, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    root.addHandler(console)
    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"screen_translator.{name}")
