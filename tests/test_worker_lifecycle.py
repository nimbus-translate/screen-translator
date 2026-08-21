"""Worker completion must be a single, unambiguous QThread event."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from app.config import AppConfig
from app.models import CaptureInfo
from workers.translation_worker import PipelineTask


class _EmptyOCR:
    def recognize(self, _image, lang="ch"):
        return []


def test_pipeline_finished_is_emitted_exactly_once(tmp_path):
    app = QApplication.instance() or QApplication([])
    capture = CaptureInfo(
        image=np.zeros((40, 80, 3), dtype=np.uint8),
        bbox=(0, 0, 80, 40),
        mode="region",
    )
    task = PipelineTask(
        capture=capture,
        ocr_engine=_EmptyOCR(),
        translator=None,
        config=AppConfig(tmp_path / "config.json"),
    )
    finished = QSignalSpy(task.finished)

    task.start()
    assert task.wait(3000)
    app.processEvents()

    assert finished.count() == 1
    task.deleteLater()
