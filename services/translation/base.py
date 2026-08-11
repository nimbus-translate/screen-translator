"""翻译抽象接口 + 缓存 / 重试 / 限速公共逻辑。"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from threading import Lock
from typing import Any

from app.logger import get_logger
from services.translation.cache import TranslationCache

log = get_logger("translation")


class TranslationError(Exception):
    def __init__(self, message: str, rate_limited: bool = False) -> None:
        super().__init__(message)
        self.rate_limited = rate_limited


_REGISTRY: dict[str, type["Translator"]] = {}


def register_translator(cls: type["Translator"]) -> None:
    _REGISTRY[cls.name] = cls


def list_translators() -> list[str]:
    return list(_REGISTRY.keys())


class Translator(ABC):
    name: str = "base"

    def __init__(
        self,
        config_section: dict,
        cache: TranslationCache | None = None,
        api_key: str = "",
    ) -> None:
        self.config = config_section or {}
        self.cache = cache
        self.api_key = api_key
        self._lock = Lock()
        self._last_request_time = 0.0
        self.last_failed_count = 0

    # ------------------------------------------------------------- 子类实现
    @abstractmethod
    def _translate_batch(self, texts: list[str], source_language: str | None, target_language: str) -> list[str]:
        """真正请求翻译服务，texts 非空且已做占位符保护。"""

    # ------------------------------------------------------------- 公共入口
    def translate(
        self,
        texts: list[str],
        source_language: str | None,
        target_language: str,
    ) -> list[str]:
        """带缓存、限速、重试的批量翻译。"""
        self.last_failed_count = 0
        if not texts:
            return []

        pending_indices: list[int] = []
        pending_texts: list[str] = []
        results: dict[int, str] = {}

        for idx, text in enumerate(texts):
            cached = self._cache_get(source_language, target_language, text)
            if cached is not None:
                results[idx] = cached
            else:
                pending_indices.append(idx)
                pending_texts.append(text)

        if pending_texts:
            translated = self._translate_with_retry(pending_texts, source_language, target_language)
            for idx, text, translated_text in zip(pending_indices, pending_texts, translated):
                results[idx] = translated_text
                self._cache_set(source_language, target_language, text, translated_text)

        return [results[i] for i in range(len(texts))]

    def _translate_with_retry(
        self, texts: list[str], source_language: str | None, target_language: str
    ) -> list[str]:
        max_retries = int(self.config.get("max_retries", 3))
        delay = float(self.config.get("retry_delay_seconds", 1.0))
        last_error: TranslationError | None = None

        for attempt in range(max_retries + 1):
            try:
                self._rate_limit()
                return self._translate_batch(texts, source_language, target_language)
            except TranslationError as exc:
                last_error = exc
                log.warning("批量翻译第 %d 次失败：%s", attempt + 1, exc)
                # 限流：重试只会继续触发 429，立即进入降级分支
                if getattr(exc, "rate_limited", False):
                    break
                if attempt < max_retries:
                    time.sleep(delay * (2**attempt))
            except Exception as exc:  # 网络等未包装异常
                last_error = TranslationError(str(exc))
                log.warning("批量翻译第 %d 次异常：%s", attempt + 1, exc)
                if attempt < max_retries:
                    time.sleep(delay * (2**attempt))

        # 公共免费服务触发 429 时，逐条重试会把一次框选拖到数分钟且最终没有覆盖层。
        # 立即返回原文，让管线正常显示结果，并由 UI 明确提示用户限流状态。
        error_text = str(last_error or "")
        if (
            getattr(last_error, "rate_limited", False)
            or "429" in error_text
            or "过于频繁" in error_text
            or "rate limit" in error_text.lower()
        ):
            self.last_failed_count = len(texts)
            log.warning("翻译服务限流，跳过逐条重试并保留原文：%d 条", len(texts))
            return list(texts)

        # 批量全挂：逐条重试，失败条目保留原文，避免整屏空白
        out: list[str] = []
        for text in texts:
            ok = False
            for attempt in range(min(max_retries, 2) + 1):
                try:
                    self._rate_limit()
                    out.append(self._translate_batch([text], source_language, target_language)[0])
                    ok = True
                    break
                except Exception as exc:
                    last_error = last_error or TranslationError(str(exc))
            if not ok:
                self.last_failed_count += 1
                out.append(text)
                log.warning("单条翻译失败，保留原文：%r", text[:40])

        if self.last_failed_count == len(texts):
            raise TranslationError(str(last_error or "翻译失败"))
        return out

    def _rate_limit(self) -> None:
        interval = float(self.config.get("request_interval_seconds", 0.0))
        if interval <= 0:
            return
        with self._lock:
            wait = interval - (time.monotonic() - self._last_request_time)
            if wait > 0:
                time.sleep(wait)
            self._last_request_time = time.monotonic()

    # ------------------------------------------------------------- 缓存
    def _cache_get(self, source, target, text) -> str | None:
        if self.cache is None:
            return None
        return self.cache.get(source, target, text)

    def _cache_set(self, source, target, text, translated) -> None:
        if self.cache is None:
            return
        self.cache.set(source, target, text, translated)
