"""Google 免费网页翻译适配器（translate.googleapis.com gtx 端点）。

特性：
- 无需 API Key、无需 Cookie / tk 令牌，匿名可用；
- 每个文本块独立请求，以低并发发送并对限流条目降速重试；
- 不做多行合并：gtx 会按语义重新断句（例如 "A. B." 会被拆成两句），
  与 OCR 文本框对不上，逐条请求才能保证行级对齐；
- 429 不写入缓存，部分失败会先串行退避重试，再由 MyMemory 兜底。
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
        configured_workers = int(cfg.get("google_free_max_workers", 4) or 4)
        # 初轮用中等并发抢速度；失败尾巴不再在 Google 上死磕，而是快速
        # 交给 MyMemory 批量兜底。这样大表格不会被一个坏块拖几十秒。
        self.max_workers = max(1, min(4, configured_workers))
        configured_interval = float(cfg.get("google_free_interval", 0.05) or 0.0)
        self.interval = max(0.05, configured_interval)
        self.partial_retries = max(
            0, min(1, int(cfg.get("google_free_partial_retries", 0) or 0))
        )
        self.retry_backoff = max(
            0.2, float(cfg.get("google_free_retry_backoff", 0.35) or 0.35)
        )
        self.fallback_to_mymemory = bool(
            cfg.get("google_free_fallback_to_mymemory", True)
        )
        self.timeout = min(6.0, float(cfg.get("timeout_seconds", 30) or 30))
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
        failed_indices: set[int] = set()
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
                    failed_indices.add(idx)
                    saw_rate_limit = saw_rate_limit or bool(exc.rate_limited)
                    last_error = exc
                    results[idx] = texts[idx]

        # 部分成功时不能把失败项直接交给基类缓存。逐轮降低到串行请求，
        # 给 429 一个退避窗口，同时保持结果索引不乱。
        for attempt in range(self.partial_retries):
            if not failed_indices:
                break
            time.sleep(self.retry_backoff * (2**attempt))
            retrying = sorted(failed_indices)
            failed_indices = set()
            for idx in retrying:
                try:
                    results[idx] = self._translate_one(texts[idx], source, target)
                except TranslationError as exc:
                    failed_indices.add(idx)
                    saw_rate_limit = saw_rate_limit or bool(exc.rate_limited)
                    last_error = exc
                    results[idx] = texts[idx]

        if failed_indices and self.fallback_to_mymemory:
            recovered = self._recover_with_mymemory(
                texts, failed_indices, source_language, target_language
            )
            for idx, translated_text in recovered.items():
                results[idx] = translated_text
                failed_indices.discard(idx)

        self.last_failed_indices = failed_indices
        self.last_failed_count = len(failed_indices)
        if len(failed_indices) == len(texts):
            raise TranslationError(
                str(last_error or "Google 免费翻译失败"), rate_limited=saw_rate_limit
            )
        return results

    def _recover_with_mymemory(
        self,
        texts: list[str],
        failed_indices: set[int],
        source_language: str | None,
        target_language: str,
    ) -> dict[int, str]:
        """Use the other anonymous provider only for Google's failed tail."""

        from services.translation.mymemory_translator import MyMemoryTranslator

        ordered_indices = sorted(failed_indices)
        fallback_texts = [texts[idx] for idx in ordered_indices]
        fallback_config = dict(self.config)
        fallback_config["max_retries"] = 0
        fallback_config["timeout_seconds"] = min(
            6.0, float(fallback_config.get("timeout_seconds", 30) or 30)
        )
        fallback_config["mymemory_min_interval"] = 0.15
        fallback = MyMemoryTranslator(fallback_config, cache=None)
        try:
            translated = fallback.translate(
                fallback_texts, source_language, target_language
            )
        except TranslationError:
            return {}

        recovered: dict[int, str] = {}
        fallback_failed = set(fallback.last_failed_indices)
        for local_index, (source_text, translated_text) in enumerate(
            zip(fallback_texts, translated)
        ):
            if (
                local_index not in fallback_failed
                and translated_text.strip() != source_text.strip()
            ):
                recovered[ordered_indices[local_index]] = translated_text
        return recovered

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
