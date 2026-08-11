"""MyMemory 适配器测试（请求打桩）。"""

import json

import requests

from services.translation.base import TranslationError
from services.translation.mymemory_translator import MyMemoryTranslator
from utils.language_utils import to_mymemory_lang


class FakeResponse:
    def __init__(self, payload, status_code=200, encoding="utf-8"):
        self._payload = payload
        self.status_code = status_code
        self.encoding = encoding
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self._payload


def test_language_mapping():
    assert to_mymemory_lang("auto") == "en"
    assert to_mymemory_lang("zh") == "zh-CN"
    assert to_mymemory_lang("zh-hant") == "zh-TW"
    assert to_mymemory_lang("ja") == "ja"


def test_translate_one(monkeypatch):
    calls = {}

    def fake_get(url, params, timeout):
        calls["url"] = url
        calls["params"] = params
        return FakeResponse(
            {"responseData": {"translatedText": "小动作的力量"}, "responseStatus": 200}
        )

    monkeypatch.setattr(requests, "get", fake_get)
    translator = MyMemoryTranslator({"timeout_seconds": 10}, None)
    result = translator._translate_one("The Power of Small Actions", "auto", "zh")
    assert result == "小动作的力量"
    assert calls["params"]["langpair"] == "en|zh-CN"


def test_translate_error(monkeypatch):
    def fake_get(url, params, timeout):
        return FakeResponse({"responseStatus": 403, "responseDetails": "denied"}, status_code=403)

    monkeypatch.setattr(requests, "get", fake_get)
    translator = MyMemoryTranslator({}, None)
    try:
        translator._translate_one("hello", "auto", "zh")
        assert False, "should raise"
    except TranslationError:
        pass


def test_unsupported_same_lang_direction():
    translator = MyMemoryTranslator({}, None)
    try:
        translator._translate_one("hello", "en", "en")
        assert False, "should raise"
    except TranslationError:
        pass


def test_429_marks_rate_limited_and_backs_off(monkeypatch):
    def fake_get(url, params, timeout):
        return FakeResponse({}, status_code=429)

    monkeypatch.setattr(requests, "get", fake_get)
    translator = MyMemoryTranslator({}, None)
    initial = translator._current_interval
    try:
        translator._translate_one("hello", "auto", "zh")
        assert False, "should raise"
    except TranslationError as exc:
        assert exc.rate_limited is True
    assert translator._current_interval == initial * 2


def test_success_streak_recovers_interval():
    translator = MyMemoryTranslator({}, None)
    translator._current_interval = 4.0
    translator._success_streak = 0
    for _ in range(20):
        translator._on_success()
    assert translator._current_interval == 2.0
    for _ in range(20):
        translator._on_success()
    assert translator._current_interval == 1.0


def test_build_batches_splits_by_chars():
    texts = ["x" * 200] * 5
    batches = MyMemoryTranslator._build_batches(texts, max_chars=450)
    assert len(batches) == 3
    assert sum(len(b) for b in batches) == 5


def test_translate_batch_joins_and_splits(monkeypatch):
    calls = []

    def fake_get(url, params, timeout):
        calls.append(params["q"])
        lines = params["q"].split("\n")
        return FakeResponse(
            {
                "responseData": {"translatedText": "\n".join("译:" + line for line in lines)},
                "responseStatus": 200,
            }
        )

    monkeypatch.setattr(requests, "get", fake_get)
    translator = MyMemoryTranslator({}, None)
    result = translator.translate(["one", "two", "three"], "auto", "zh")
    assert result == ["译:one", "译:two", "译:three"]
    assert len(calls) == 1


def test_translate_batch_line_mismatch_falls_back(monkeypatch):
    calls = []

    def fake_get(url, params, timeout):
        calls.append(params["q"])
        if "\n" in params["q"]:
            return FakeResponse(
                {"responseData": {"translatedText": "单行合并结果"}, "responseStatus": 200}
            )
        return FakeResponse(
            {"responseData": {"translatedText": "译:" + params["q"]}, "responseStatus": 200}
        )

    monkeypatch.setattr(requests, "get", fake_get)
    translator = MyMemoryTranslator({}, None)
    result = translator.translate(["a", "b"], "auto", "zh")
    assert result == ["译:a", "译:b"]
    # 1 次合并请求 + 2 次逐条兜底
    assert len(calls) == 3
