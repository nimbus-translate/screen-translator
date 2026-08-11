"""翻译重试与失败降级测试。"""

import pytest

from services.translation.base import TranslationError, Translator


class FlakyTranslator(Translator):
    name = "flaky"

    def __init__(self, fail_times=0, fail_forever=False):
        super().__init__({"max_retries": 2, "retry_delay_seconds": 0.01})
        self.fail_times = fail_times
        self.fail_forever = fail_forever
        self.calls = 0

    def _translate_batch(self, texts, source_language, target_language):
        self.calls += 1
        if self.fail_forever or self.calls <= self.fail_times:
            raise TranslationError("boom")
        return [f"译:{t}" for t in texts]


def test_retry_succeeds_after_failures():
    translator = FlakyTranslator(fail_times=1)
    result = translator.translate(["hello"], "en", "zh")
    assert result == ["译:hello"]
    assert translator.last_failed_count == 0


def test_total_failure_raises():
    translator = FlakyTranslator(fail_forever=True)
    with pytest.raises(TranslationError):
        translator.translate(["hello"], "en", "zh")


def test_partial_failure_keeps_original():
    class PartialTranslator(Translator):
        name = "partial"

        def __init__(self):
            super().__init__({"max_retries": 0, "retry_delay_seconds": 0})
            self.fail_first = True

        def _translate_batch(self, texts, source_language, target_language):
            if self.fail_first and len(texts) == 2:
                self.fail_first = False
                raise TranslationError("partial fail")
            return [f"译:{t}" for t in texts]

    translator = PartialTranslator()
    result = translator.translate(["a", "b"], "en", "zh")
    assert len(result) == 2
    assert result[0] == "译:a" or result[1] == "译:b"


def test_rate_limited_fails_fast_without_retry():
    class RateLimitedTranslator(Translator):
        name = "ratelimited"

        def __init__(self):
            super().__init__({"max_retries": 3, "retry_delay_seconds": 0.01})
            self.calls = 0

        def _translate_batch(self, texts, source_language, target_language):
            self.calls += 1
            raise TranslationError("429 too many", rate_limited=True)

    translator = RateLimitedTranslator()
    result = translator.translate(["hello"], "en", "zh")
    assert result == ["hello"]  # 保留原文
    assert translator.calls == 1  # 不重试
    assert translator.last_failed_count == 1
