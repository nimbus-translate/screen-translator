"""Google 免费翻译适配器测试（请求打桩，不访问网络）。"""

from __future__ import annotations

import pytest

from services.translation.base import TranslationError, list_translators
from services.translation.google_free_translator import GoogleFreeTranslator
from utils.language_utils import to_google_lang


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = repr(payload)

    def json(self):
        return self._payload


def _gtx_payload(translated: str) -> list:
    return [[[translated, "src", None, None, 0]], None, "en", "zh-CN"]


def test_language_mapping():
    assert to_google_lang("auto") == ""
    assert to_google_lang("zh") == "zh-CN"
    assert to_google_lang("zh-hant") == "zh-TW"
    assert to_google_lang("ja") == "ja"


def test_registered():
    assert "google_free" in list_translators()


def test_translate_one_parses_segments(monkeypatch):
    translator = GoogleFreeTranslator({"timeout_seconds": 10}, None)
    seen = {}

    def fake_get(url, params, timeout):
        seen["url"] = url
        seen["params"] = params
        return FakeResponse(_gtx_payload("你好，世界"))

    monkeypatch.setattr(translator._session, "get", fake_get)
    result = translator._translate_one("Hello, world", "auto", "zh-CN")
    assert result == "你好，世界"
    assert seen["params"]["client"] == "gtx"
    assert seen["params"]["sl"] == "auto"
    assert seen["params"]["tl"] == "zh-CN"
    assert seen["params"]["q"] == "Hello, world"


def test_translate_maps_target_language(monkeypatch):
    translator = GoogleFreeTranslator({}, None)
    seen = {}

    def fake_get(url, params, timeout):
        seen["params"] = params
        return FakeResponse(_gtx_payload("译:" + params["q"]))

    monkeypatch.setattr(translator._session, "get", fake_get)
    translator.translate(["hi"], "auto", "zh")
    assert seen["params"]["tl"] == "zh-CN"


def test_translate_one_joins_multiple_segments(monkeypatch):
    translator = GoogleFreeTranslator({}, None)

    def fake_get(url, params, timeout):
        payload = [[["第一段", "a", None, None, 0], ["第二段", "b", None, None, 0]], None, "en", "zh-CN"]
        return FakeResponse(payload)

    monkeypatch.setattr(translator._session, "get", fake_get)
    assert translator._translate_one("a b", "auto", "zh") == "第一段第二段"


def test_translate_one_unescapes_html(monkeypatch):
    translator = GoogleFreeTranslator({}, None)

    def fake_get(url, params, timeout):
        return FakeResponse(_gtx_payload("他说：&quot;你好&quot;"))

    monkeypatch.setattr(translator._session, "get", fake_get)
    assert translator._translate_one("He said: \"hi\"", "auto", "zh") == "他说：\"你好\""


def test_translate_preserves_order_with_concurrency(monkeypatch):
    translator = GoogleFreeTranslator({}, None)

    def fake_get(url, params, timeout):
        return FakeResponse(_gtx_payload("译:" + params["q"]))

    monkeypatch.setattr(translator._session, "get", fake_get)
    result = translator.translate(["one", "two", "three"], "auto", "zh")
    assert result == ["译:one", "译:two", "译:three"]
    assert translator.last_failed_count == 0


def test_partial_failure_keeps_original(monkeypatch):
    translator = GoogleFreeTranslator({}, None)

    def fake_get(url, params, timeout):
        if params["q"] == "bad":
            return FakeResponse({}, status_code=500)
        return FakeResponse(_gtx_payload("译:" + params["q"]))

    monkeypatch.setattr(translator._session, "get", fake_get)
    result = translator.translate(["good", "bad", "ok"], "auto", "zh")
    assert result == ["译:good", "bad", "译:ok"]
    assert translator.last_failed_count == 1


def test_all_429_marks_rate_limited(monkeypatch):
    translator = GoogleFreeTranslator({}, None)

    def fake_get(url, params, timeout):
        return FakeResponse({}, status_code=429)

    monkeypatch.setattr(translator._session, "get", fake_get)
    result = translator.translate(["a", "b"], "auto", "zh")
    assert result == ["a", "b"]  # 保留原文，不弹错误
    assert translator.last_failed_count == 2


def test_all_failure_raises_with_429_flag(monkeypatch):
    translator = GoogleFreeTranslator({}, None)

    def fake_get(url, params, timeout):
        return FakeResponse({}, status_code=429)

    monkeypatch.setattr(translator._session, "get", fake_get)
    with pytest.raises(TranslationError) as exc_info:
        translator._translate_batch(["a"], "auto", "zh")
    assert exc_info.value.rate_limited is True


def test_bad_response_format_raises(monkeypatch):
    translator = GoogleFreeTranslator({}, None)

    def fake_get(url, params, timeout):
        return FakeResponse({"error": "boom"})

    monkeypatch.setattr(translator._session, "get", fake_get)
    with pytest.raises(TranslationError):
        translator._translate_one("hi", "auto", "zh")


def test_empty_text_returns_empty():
    translator = GoogleFreeTranslator({}, None)
    assert translator._translate_one("  ", "auto", "zh") == ""
