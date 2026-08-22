"""Regression tests for keeping captured OCR text out of errors and logs."""

from __future__ import annotations

import logging
from collections.abc import Callable

import pytest
import requests

from services.translation.base import TranslationError, Translator
from services.translation.deepl_translator import DeepLTranslator
from services.translation.google_free_translator import GoogleFreeTranslator
from services.translation.google_translator import GoogleTranslator
from services.translation.mymemory_translator import MyMemoryTranslator
from services.translation.openai_translator import OpenAITranslator


_OCR_TEXT = "PRIVATE_OCR_TEXT_7f91"
_LEAKY_REQUEST = (
    "GET https://translate.example.test/request?client=gtx"
    f"&q={_OCR_TEXT}&key=not-for-logs"
)


def _assert_private_data_absent(text: str) -> None:
    assert _OCR_TEXT not in text
    assert "q=" not in text
    assert "https://translate.example.test" not in text


class _UnexpectedFailureTranslator(Translator):
    name = "unexpected_failure"

    def __init__(self) -> None:
        super().__init__({"max_retries": 0, "retry_delay_seconds": 0})

    def _translate_batch(self, texts, source_language, target_language):
        raise RuntimeError(_LEAKY_REQUEST)


def test_unexpected_failure_logs_only_exception_type(caplog):
    translator = _UnexpectedFailureTranslator()

    with caplog.at_level(logging.WARNING, logger="screen_translator.translation"):
        with pytest.raises(TranslationError) as exc_info:
            translator.translate([_OCR_TEXT], "en", "zh")

    rendered_logs = "\n".join(record.getMessage() for record in caplog.records)
    _assert_private_data_absent(rendered_logs)
    _assert_private_data_absent(str(exc_info.value))
    assert "RuntimeError" in rendered_logs


_RequestCall = Callable[[Translator], object]


@pytest.mark.parametrize(
    ("factory", "request_method", "call"),
    [
        (
            lambda: GoogleTranslator({"google": {"api_key": "configured"}}),
            "get",
            lambda translator: translator._translate_batch([_OCR_TEXT], "en", "zh"),
        ),
        (
            lambda: DeepLTranslator({"deepl": {"api_key": "configured"}}),
            "post",
            lambda translator: translator._translate_batch([_OCR_TEXT], "en", "zh"),
        ),
        (
            lambda: OpenAITranslator({"openai": {"api_key": "configured"}}),
            "post",
            lambda translator: translator._translate_batch([_OCR_TEXT], "en", "zh"),
        ),
        (
            lambda: MyMemoryTranslator({}),
            "get",
            lambda translator: translator._translate_one(_OCR_TEXT, "en", "zh"),
        ),
        (
            lambda: GoogleFreeTranslator({}),
            "session_get",
            lambda translator: translator._translate_one(_OCR_TEXT, "en", "zh"),
        ),
    ],
    ids=["google", "deepl", "openai", "mymemory", "google-free"],
)
def test_request_exception_does_not_surface_url_or_ocr_text(
    monkeypatch,
    factory: Callable[[], Translator],
    request_method: str,
    call: _RequestCall,
):
    translator = factory()

    def fail_request(*args, **kwargs):
        raise requests.RequestException(_LEAKY_REQUEST)

    if request_method == "session_get":
        monkeypatch.setattr(translator._session, "get", fail_request)
    else:
        monkeypatch.setattr(requests, request_method, fail_request)

    with pytest.raises(TranslationError) as exc_info:
        call(translator)

    _assert_private_data_absent(str(exc_info.value))


class _ErrorResponse:
    status_code = 500
    encoding = "utf-8"
    text = _LEAKY_REQUEST


@pytest.mark.parametrize(
    ("factory", "request_method", "call"),
    [
        (
            lambda: GoogleTranslator({"google": {"api_key": "configured"}}),
            "get",
            lambda translator: translator._translate_batch([_OCR_TEXT], "en", "zh"),
        ),
        (
            lambda: DeepLTranslator({"deepl": {"api_key": "configured"}}),
            "post",
            lambda translator: translator._translate_batch([_OCR_TEXT], "en", "zh"),
        ),
        (
            lambda: OpenAITranslator({"openai": {"api_key": "configured"}}),
            "post",
            lambda translator: translator._translate_batch([_OCR_TEXT], "en", "zh"),
        ),
    ],
    ids=["google", "deepl", "openai"],
)
def test_error_response_body_does_not_surface_ocr_text(
    monkeypatch,
    factory: Callable[[], Translator],
    request_method: str,
    call: _RequestCall,
):
    monkeypatch.setattr(requests, request_method, lambda *args, **kwargs: _ErrorResponse())

    with pytest.raises(TranslationError) as exc_info:
        call(factory())

    _assert_private_data_absent(str(exc_info.value))
