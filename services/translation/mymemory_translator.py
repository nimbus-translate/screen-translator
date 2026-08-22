"""MyMemory 免费在线翻译适配器（免注册、免 Key，匿名限速）。

提速策略：多个文本块合并成一条请求（换行分隔，单条上限约 500 字符），
一次翻译一整批再按行拆回，请求数减少一个数量级。
"""

from __future__ import annotations

import threading
import time

import requests

from services.translation.base import TranslationError, Translator, register_translator
from utils.language_utils import to_mymemory_lang

log = None


def _get_logger():
    global log
    if log is None:
        from app.logger import get_logger

        log = get_logger("translation.mymemory")
    return log


class MyMemoryTranslator(Translator):
    name = "mymemory"

    def __init__(self, config_section: dict, cache=None, api_key: str = "") -> None:
        super().__init__(config_section, cache, api_key)
        self.base_url = "https://api.mymemory.translated.net/get"
        # 匿名接口限速：官方建议约 2 请求/秒；遇到 429 会自动翻倍放慢
        self._current_interval = float(config_section.get("mymemory_min_interval", 0.5)) if config_section else 0.5
        self._max_interval = 10.0
        self._success_streak = 0
        self._max_chars_per_request = 450
        self._last_call = 0.0
        self._pace_lock = threading.Lock()

    def _translate_batch(self, texts: list[str], source_language: str | None, target_language: str) -> list[str]:
        results: list[str] = []
        failed_indices: set[int] = set()
        for batch in self._build_batches(texts):
            self._pace()
            translated = self._translate_joined(batch, source_language, target_language)
            if len(translated) == len(batch):
                batch_results = translated
            else:
                # 行数对不上（MyMemory 偶尔合并/拆分句子）：该批降级为逐条翻译
                batch_results = []
                for text in batch:
                    self._pace()
                    batch_results.append(
                        self._translate_one(text, source_language, target_language)
                    )

            # MyMemory 还会用 200 响应原样返回其中几行。只把这些可疑项
            # 拆出来逐条重译，避免整页退化成 20~40 次慢请求。
            for local_index, (source_text, translated_text) in enumerate(
                zip(batch, batch_results)
            ):
                if translated_text.strip() != source_text.strip():
                    continue
                try:
                    self._pace()
                    batch_results[local_index] = self._translate_one(
                        source_text, source_language, target_language
                    )
                except TranslationError:
                    failed_indices.add(len(results) + local_index)
                    continue
                if batch_results[local_index].strip() == source_text.strip():
                    failed_indices.add(len(results) + local_index)
            results.extend(batch_results)

        self.last_failed_indices = failed_indices
        self.last_failed_count = len(failed_indices)
        return results

    @staticmethod
    def _build_batches(texts: list[str], max_chars: int = 450) -> list[list[str]]:
        batches: list[list[str]] = []
        current: list[str] = []
        size = 0
        for text in texts:
            length = len(text) + 1  # 换行符
            if current and size + length > max_chars:
                batches.append(current)
                current = []
                size = 0
            current.append(text)
            size += length
        if current:
            batches.append(current)
        return batches

    def _translate_joined(self, batch: list[str], source_language: str | None, target_language: str) -> list[str]:
        joined = "\n".join(batch)
        translated = self._translate_one(joined, source_language, target_language)
        lines = [line.strip() for line in translated.split("\n")]
        return [line for line in lines if line]

    def _translate_one(self, text: str, source_language: str | None, target_language: str) -> str:
        source = to_mymemory_lang(source_language) if source_language and source_language != "auto" else "en"
        target = to_mymemory_lang(target_language)
        if not target or target == source:
            raise TranslationError(f"MyMemory 不支持 {source_language or 'auto'} → {target_language} 方向")
        try:
            response = requests.get(
                self.base_url,
                params={"q": text, "langpair": f"{source}|{target}"},
                timeout=float(self.config.get("timeout_seconds", 30)),
            )
        except requests.RequestException as exc:
            raise TranslationError("MyMemory 请求失败（网络连接异常）") from exc
        response.encoding = "utf-8"
        if response.status_code == 429:
            self._on_rate_limited()
            raise TranslationError("MyMemory 请求过于频繁（429），已自动放慢节奏", rate_limited=True)
        if response.status_code != 200:
            raise TranslationError(f"MyMemory 返回错误 {response.status_code}")
        try:
            data = response.json()
            response_status = int(data.get("responseStatus", 0))
            if response_status != 200:
                raise TranslationError(f"MyMemory 返回错误 {response_status}")
            translated = data["responseData"]["translatedText"]
        except (KeyError, ValueError) as exc:
            raise TranslationError("MyMemory 响应格式异常") from exc
        if not translated or not translated.strip():
            raise TranslationError("MyMemory 返回空译文")
        self._on_success()
        return translated

    def _pace(self) -> None:
        with self._pace_lock:
            wait = self._current_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()

    def _on_rate_limited(self) -> None:
        self._current_interval = min(self._current_interval * 2.0, self._max_interval)
        self._success_streak = 0
        _get_logger().warning("MyMemory 429，本次请求间隔调整为 %.1fs", self._current_interval)

    def _on_success(self) -> None:
        self._success_streak += 1
        # 连续成功一段时间后逐步恢复默认节奏
        if self._success_streak >= 20 and self._current_interval > 0.5:
            self._current_interval = max(0.5, self._current_interval / 2.0)
            self._success_streak = 0


register_translator(MyMemoryTranslator)
