from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from services.ocr import windows_ocr


def _pretend_windows(monkeypatch):
    monkeypatch.setattr(windows_ocr.sys, "platform", "win32")


def test_available_is_false_with_actionable_diagnostic_when_dependency_is_missing(monkeypatch):
    _pretend_windows(monkeypatch)

    def missing(_name):
        raise ModuleNotFoundError("No module named 'winocr'")

    monkeypatch.setattr(windows_ocr.importlib, "import_module", missing)

    assert not windows_ocr.WindowsOCREngine.available()
    assert "pip install winocr" in windows_ocr.WindowsOCREngine.availability_reason()


def test_available_rejects_non_windows_without_importing_optional_dependency(monkeypatch):
    monkeypatch.setattr(windows_ocr.sys, "platform", "linux")
    monkeypatch.setattr(
        windows_ocr.importlib,
        "import_module",
        lambda _name: pytest.fail("non-Windows availability check must not import winocr"),
    )

    assert not windows_ocr.WindowsOCREngine.available()
    assert windows_ocr.WindowsOCREngine.availability_reason() == "Windows OCR 仅支持 Windows 10/11"


def test_available_accepts_the_sync_winocr_api(monkeypatch):
    _pretend_windows(monkeypatch)
    fake_winocr = SimpleNamespace(recognize_pil_sync=lambda *_args: None)
    monkeypatch.setattr(windows_ocr.importlib, "import_module", lambda _name: fake_winocr)

    assert windows_ocr.WindowsOCREngine.available()
    assert windows_ocr.WindowsOCREngine.availability_reason() is None


def test_recognize_converts_bgr_and_returns_project_ocr_lines(monkeypatch):
    calls = []

    def recognize(image, language):
        calls.append((image.getpixel((0, 0)), language))
        return {
            "lines": [
                {
                    "words": [
                        {"text": "hello", "bounding_rect": {"x": 3, "y": 5, "width": 10, "height": 7}},
                        {"text": "world", "bounding_rect": {"x": 14, "y": 4, "width": 8, "height": 9}},
                    ]
                },
                {"text": "unboxed", "words": []},
            ]
        }

    engine = windows_ocr.WindowsOCREngine({"lang": "en"})
    monkeypatch.setattr(
        engine,
        "_load_winocr",
        lambda: SimpleNamespace(recognize_pil_sync=recognize),
    )
    image_bgr = np.array([[[10, 20, 30]]], dtype=np.uint8)

    lines = engine.recognize(image_bgr)

    assert calls == [((30, 20, 10), "en-US")]
    assert [(line.text, line.box, line.confidence) for line in lines] == [
        ("hello world", (3, 4, 19, 9), 1.0),
        ("unboxed", (0, 0, 0, 0), 1.0),
    ]


def test_recognize_awaits_winrt_api_and_auto_falls_back_to_next_language(monkeypatch):
    calls = []

    async def recognize(_image, language):
        calls.append(language)
        if language == "zh-Hans-CN":
            raise RuntimeError("language pack missing")
        return SimpleNamespace(
            lines=[
                SimpleNamespace(
                    text="English",
                    words=[
                        SimpleNamespace(
                            text="English",
                            bounding_rect=SimpleNamespace(x=1, y=2, width=3, height=4),
                        )
                    ],
                )
            ]
        )

    engine = windows_ocr.WindowsOCREngine({"lang": "auto"})
    monkeypatch.setattr(engine, "_load_winocr", lambda: SimpleNamespace(recognize_pil=recognize))

    lines = engine.recognize(np.zeros((2, 2, 3), dtype=np.uint8))

    assert calls == ["zh-Hans-CN", "en-US"]
    assert [(line.text, line.box) for line in lines] == [("English", (1, 2, 3, 4))]


def test_auto_mode_does_not_stop_on_an_empty_first_result(monkeypatch):
    calls = []

    def recognize(_image, language):
        calls.append(language)
        if language == "zh-Hans-CN":
            return {"lines": []}
        return {"lines": [{"text": "English paragraph", "words": []}]}

    engine = windows_ocr.WindowsOCREngine({"lang": "auto"})
    monkeypatch.setattr(
        engine, "_load_winocr", lambda: SimpleNamespace(recognize_pil_sync=recognize)
    )

    lines = engine.recognize(np.zeros((4, 4, 3), dtype=np.uint8))

    assert calls == ["zh-Hans-CN", "en-US"]
    assert [line.text for line in lines] == ["English paragraph"]


def test_auto_mode_uses_installed_language_candidates_and_picks_cleaner_text(monkeypatch):
    calls = []
    installed = [
        SimpleNamespace(language_tag="zh-Hans-CN"),
        SimpleNamespace(language_tag="ja-JP"),
    ]

    def recognize(_image, language):
        calls.append(language)
        text = {
            "zh-Hans-CN": "Last Sunday, | had a happy day and ] went home.",
            "ja-JP": "Last Sunday, I had a happy day and I went home.",
        }[language]
        return {"lines": [{"text": text, "words": []}]}

    fake_engine_type = SimpleNamespace(available_recognizer_languages=installed)
    fake_winocr = SimpleNamespace(
        OcrEngine=fake_engine_type,
        recognize_pil_sync=recognize,
    )
    engine = windows_ocr.WindowsOCREngine({"lang": "auto"})
    monkeypatch.setattr(engine, "_load_winocr", lambda: fake_winocr)

    lines = engine.recognize(np.zeros((4, 4, 3), dtype=np.uint8))

    assert calls == ["zh-Hans-CN", "ja-JP"]
    assert [line.text for line in lines] == [
        "Last Sunday, I had a happy day and I went home."
    ]
    assert engine.last_language_tag == "ja-JP"


@pytest.mark.parametrize(
    "image, message",
    [
        (np.zeros((2, 2), dtype=np.uint8), "H×W×3"),
        (np.zeros((0, 2, 3), dtype=np.uint8), "空图像"),
        (np.zeros((2, 2, 3), dtype=np.float32), "uint8"),
    ],
)
def test_recognize_rejects_invalid_bgr_input(image, message):
    with pytest.raises(ValueError, match=message):
        windows_ocr.WindowsOCREngine._to_pil_rgb(image)
