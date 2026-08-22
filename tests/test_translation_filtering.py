"""Skip non-translatable screen regions before they can dirty the overlay."""

from __future__ import annotations

import numpy as np

from app.config import AppConfig
from app.models import CaptureInfo
from services.ocr.base import OCRLine
from utils.language_utils import needs_translation
from workers.translation_worker import PipelineTask


def test_target_script_and_ui_glyphs_are_not_translated():
    assert not needs_translation("设置 GitHub 双因素认证", "zh")
    assert not needs_translation("0", "zh")
    assert not needs_translation("O", "zh")
    assert not needs_translation(".github/workflows", "zh")
    assert not needs_translation("README.md", "zh")
    assert not needs_translation("e8483e8", "zh")
    assert not needs_translation("Claude", "zh")
    assert not needs_translation("stripe", "zh")
    assert not needs_translation("0 Spotify", "zh")
    assert not needs_translation("Banner Health", "zh")
    assert not needs_translation("SWE-Bench Pro", "zh")
    assert not needs_translation("GDPval-AA", "zh")
    assert not needs_translation("Legal Agent Benchmark", "zh")
    assert not needs_translation("CURSOR", "zh")
    assert needs_translation("CursorBench. It's opened up a class of long-horizon problems", "zh")
    assert needs_translation("benchmarks. But what excites us most is the direction", "zh")
    assert not needs_translation("xhigh", "zh")
    assert needs_translation("Settings", "zh")
    assert needs_translation("3 Commits", "zh")
    assert needs_translation(
        "模型界面残留 our strong dedication to customer excellence", "zh"
    )
    assert not needs_translation("使用 GitHub Actions 自动构建版本", "zh")


class _OCR:
    def recognize(self, _image, lang="auto"):
        return [
            OCRLine("设置", (5, 5, 24, 16), 1.0),
            OCRLine("Settings", (50, 5, 70, 16), 1.0),
            OCRLine("0", (140, 5, 16, 16), 1.0),
        ]


class _Translator:
    last_failed_count = 0
    unsupported_direction = False

    def __init__(self) -> None:
        self.calls = []

    def translate(self, texts, source, target):
        self.calls.append((list(texts), source, target))
        return ["设置"]


def test_pipeline_only_sends_meaningful_foreign_text(tmp_path):
    capture = CaptureInfo(
        image=np.full((40, 180, 3), 255, dtype=np.uint8),
        bbox=(0, 0, 180, 40),
        monitor_indices=[0],
        mode="region",
    )
    translator = _Translator()
    task = PipelineTask(
        capture,
        _OCR(),
        translator,
        AppConfig(tmp_path / "config.json"),
    )
    results = []
    task.result.connect(results.append)

    task.run()

    assert translator.calls == [(["Settings"], "auto", "zh")]
    regions = results[0]["regions"]
    assert results[0]["recognized_count"] == 3
    assert results[0]["translation_candidate_count"] == 1
    assert results[0]["translated_count"] == 1
    assert [(region.text, region.translated_text) for region in regions] == [
        ("设置", "设置"),
        ("Settings", "设置"),
        ("0", "0"),
    ]
