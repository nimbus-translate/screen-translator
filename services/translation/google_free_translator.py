"""Google 免费网页翻译适配器（translate.googleapis.com gtx 端点）。

特性：
- 无需 API Key、无需 Cookie / tk 令牌，匿名可用；
- 每个文本块独立请求，使用线程池并发（默认 8），25 个块约 1.5-3 秒；
- 不做多行合并：gtx 会按语义重新断句（例如 "A. B." 会被拆成两句），
  与 OCR 文本框对不上，逐条请求才能保证行级对齐；
- 429 快速失败并标记限流，让基类跳过重试直接保留原文。
"""

from __future__ import annotations

import concurrent.futures as cf
import html
import threading
import time

import requests
from requests.adapters import HTTPAdapter

from services.translation.base import TranslationError, Translator, register_translator
from utils.language_utils import to_google_lang


class GoogleFreeTranslator(Translator):
    name = "google_free"

    def __init__(self, config_section: dict, cache=None, api_key: str = "") -> None:
        super().__init__(config_section, cache, api_key)
        cfg = config_section or {}
        self.base_url = "https://translate.googleapis.com/translate_a/single"
        self.max_workers = int(cfg.get("google_free_max_workers", 8) or 8)
        self.interval = float(cfg.get("google_free_interval", 0.0) or 0.0)
        self.timeout = float(cfg.get("timeout_seconds", 30) or 30)
        self._session = requests.Session()
        adapter = HTTPAdapter(pool_connections=self.max_workers, pool_maxsize=self.max_workers)
        self._session.mount("https://", adapter)
        self._last_call = 0.0
        self._pace_lock = threading.Lock()

    # ------------------------------------------------------------------ 实现
    def _translate_batch(
        self, texts: list[str], source_language: str | None, target_language: str
    ) -> list[str]:
        if not texts:
            return []
        source = to_google_lang(source_language) if source_language and source_language != "auto" else "auto"
        target = to_google_lang(target_language)
        if not target:
            raise TranslationError(f"Google 免费翻译不支持目标语言：{target_language}")

        results: list[str] = [""] * len(texts)
        failures = 0
        saw_rate_limit = False
        last_error: TranslationError | None = None

        with cf.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_idx = {
                executor.submit(self._translate_one, text, source, target): idx
                for idx, text in enumerate(texts)
            }
            for future in cf.as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    results[idx] = future.result()
                except TranslationError as exc:
                    failures += 1
                    saw_rate_limit = saw_rate_limit or bool(exc.rate_limited)
                    last_error = exc
                    results[idx] = texts[idx]

        self.last_failed_count = failures
        if failures == len(texts):
            raise TranslationError(
                str(last_error or "Google 免费翻译失败"), rate_limited=saw_rate_limit
            )
        return results

    def _translate_one(self, text: str, source: str, target: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        params = {
            "client": "gtx",
            "sl": source,
            "tl": target,
            "dt": "t",
            "q": text,
        }
        for attempt in range(2):
            self._pace()
            try:
                response = self._session.get(
                    self.base_url, params=params, timeout=self.timeout
                )
            except requests.RequestException as exc:
                if attempt == 0:
                    continue
                raise TranslationError("Google 免费翻译请求失败（网络连接异常）") from exc
            if response.status_code == 429:
                raise TranslationError("Google 免费翻译请求过于频繁（429），已保留原文", rate_limited=True)
            if response.status_code >= 500 and attempt == 0:
                time.sleep(0.3)
                continue
            if response.status_code != 200:
                raise TranslationError(f"Google 免费翻译返回错误 {response.status_code}")
            try:
                data = response.json()
                translated = "".join(
                    segment[0] for segment in data[0] if segment and segment[0]
                )
            except (IndexError, KeyError, TypeError, ValueError) as exc:
                raise TranslationError("Google 免费翻译响应格式异常") from exc
            if not translated.strip():
                raise TranslationError("Google 免费翻译返回空译文")
            return html.unescape(translated)
        raise TranslationError("Google 免费翻译请求失败")

    # ------------------------------------------------------------------ 节流
    def _pace(self) -> None:
        if self.interval <= 0:
            return
        with self._pace_lock:
            wait = self.interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()


register_translator(GoogleFreeTranslator)
