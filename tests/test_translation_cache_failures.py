"""Failed or poisoned translations must never become durable cache hits."""

from services.translation.base import Translator
from services.translation.cache import TranslationCache


class _Translator(Translator):
    name = "cache-test"

    def __init__(self, cache, translated):
        super().__init__({}, cache)
        self.translated = translated
        self.calls = []

    def _translate_batch(self, texts, source_language, target_language):
        self.calls.append(list(texts))
        return list(self.translated)


def test_identical_cross_language_cache_entry_is_retranslated(tmp_path):
    cache = TranslationCache(tmp_path / "cache.json")
    cache.set("auto", "zh", "Main article", "Main article")
    translator = _Translator(cache, ["主要文章"])

    assert translator.translate(["Main article"], "auto", "zh") == ["主要文章"]
    assert translator.calls == [["Main article"]]
    assert cache.get("auto", "zh", "Main article") == "主要文章"


def test_failed_indices_are_not_cached(tmp_path):
    cache = TranslationCache(tmp_path / "cache.json")
    translator = _Translator(cache, ["成功", "failed"])

    def partial(texts, source_language, target_language):
        translator.last_failed_count = 1
        translator.last_failed_indices = {1}
        return ["成功", "failed"]

    translator._translate_batch = partial
    assert translator.translate(["success", "failed"], "auto", "zh") == [
        "成功",
        "failed",
    ]
    assert cache.get("auto", "zh", "success") == "成功"
    assert cache.get("auto", "zh", "failed") is None
