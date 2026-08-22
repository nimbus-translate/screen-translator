"""Shutdown and moving-target confirmation regressions."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import QEventLoop, QThread, QTimer
from PySide6.QtWidgets import QApplication

import app.application as application_module
import ui.update_tasks as update_tasks_module
from app.application import Application, _CaptureSession
from app.config import AppConfig
from app.models import CaptureInfo
from services.ocr.base import OCRUnavailableError
from services.update_service import VerifiedDownload
from ui.ocr_component_tasks import PaddleComponentInstallTask
from ui.update_tasks import UpdateCheckTask, UpdateDownloadTask
from workers.translation_worker import PipelineTask


class _SlowOCR:
    def recognize(self, _image, lang="ch"):
        loop = QEventLoop()
        QTimer.singleShot(60, loop.quit)
        loop.exec()
        return []


class _BlockingTaskMixin:
    """Simulate a network read which observes cancellation only after unblocking."""

    def _init_blocker(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True

    def run(self) -> None:
        self.started.set()
        self.release.wait(2)


class _BlockingUpdateCheckTask(_BlockingTaskMixin, UpdateCheckTask):
    def __init__(self, parent) -> None:
        QThread.__init__(self, parent)
        self._init_blocker()


class _BlockingUpdateDownloadTask(_BlockingTaskMixin, UpdateDownloadTask):
    def __init__(self, parent) -> None:
        QThread.__init__(self, parent)
        self._init_blocker()


class _BlockingPaddleComponentTask(_BlockingTaskMixin, PaddleComponentInstallTask):
    def __init__(self, parent) -> None:
        QThread.__init__(self, parent)
        self._init_blocker()


def _drain_events_until(app, predicate, *, timeout_seconds: float = 2.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    app.processEvents()
    assert predicate(), "timed out waiting for queued Qt completion"


def test_shutdown_waits_for_real_worker_finished_before_quitting(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("SCREEN_TRANSLATOR_CONFIG", str(tmp_path / "config.json"))
    controller = Application(app)
    capture = CaptureInfo(
        image=np.zeros((40, 80, 3), dtype=np.uint8),
        bbox=(0, 0, 80, 40),
        mode="region",
    )
    controller.worker = PipelineTask(
        capture=capture,
        ocr_engine=_SlowOCR(),
        translator=None,
        config=AppConfig(tmp_path / "config.json"),
    )
    quits = []
    monkeypatch.setattr(app, "quit", lambda: quits.append(True))
    controller.worker.start()

    controller.shutdown()

    assert controller._shutting_down
    assert controller.worker is not None
    assert quits == []
    assert controller.worker.wait(3000)
    app.processEvents()
    assert controller.worker is None
    assert quits == [True]
    controller.floating_status.deleteLater()


def test_shutdown_waits_for_cancelled_auxiliary_qthreads_before_quitting(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("SCREEN_TRANSLATOR_CONFIG", str(tmp_path / "config.json"))
    controller = Application(app)
    tasks = [
        _BlockingUpdateCheckTask(app),
        _BlockingUpdateDownloadTask(app),
        _BlockingPaddleComponentTask(app),
    ]
    # Only the app-owned task is explicitly referenced by Application.  The
    # other two prove shutdown discovers settings-owned task types too.
    controller._update_check_task = tasks[0]
    quits = []
    monkeypatch.setattr(app, "quit", lambda: quits.append(True))

    for task in tasks:
        task.start()
    assert all(task.started.wait(1) for task in tasks)

    controller.shutdown()

    assert all(task.cancelled for task in tasks)
    assert all(task.isRunning() for task in tasks)
    assert controller._shutdown_aux_tasks == tasks
    assert quits == []

    for task in tasks:
        task.release.set()
        assert task.wait(2000)
    _drain_events_until(app, lambda: controller._shutdown_finalized)

    assert controller.worker is None
    assert quits == [True]
    for task in tasks:
        task.deleteLater()
    controller.floating_status.deleteLater()
    app.processEvents()


def test_install_update_rejects_file_changed_after_verified_download(tmp_path, monkeypatch):
    """The controller must not launch a file whose expected digest changed."""
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("SCREEN_TRANSLATOR_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setattr(application_module, "app_data_dir", lambda: tmp_path)
    controller = Application(app)
    update_dir = tmp_path / "updates"
    update_dir.mkdir()
    installer = update_dir / "ScreenTranslator-Lite-setup.exe"
    installer.write_bytes(b"verified installer")
    expected_digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    installer.write_bytes(b"replaced after verification")
    launched = []
    errors = []
    monkeypatch.setattr(application_module.subprocess, "Popen", lambda *args, **kwargs: launched.append((args, kwargs)))
    monkeypatch.setattr(controller, "show_error", lambda title, message: errors.append((title, message)))

    controller.install_update(str(installer), expected_digest)

    assert launched == []
    assert errors == [("更新失败", "更新包在下载后发生变化，已拒绝执行")]
    controller.floating_status.deleteLater()
    app.processEvents()


def test_install_update_rejects_untrusted_authenticode_signature(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("SCREEN_TRANSLATOR_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setattr(application_module, "app_data_dir", lambda: tmp_path)
    controller = Application(app)
    update_dir = tmp_path / "updates"
    update_dir.mkdir()
    installer = update_dir / "ScreenTranslator-Lite-setup.exe"
    installer.write_bytes(b"checksum-valid but unsigned")
    expected_digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    launched = []
    errors = []

    def reject(*_args, **_kwargs):
        raise application_module.AuthenticodeVerificationError("NotSigned")

    monkeypatch.setattr(application_module, "verify_authenticode", reject)
    monkeypatch.setattr(
        application_module.subprocess,
        "Popen",
        lambda *args, **kwargs: launched.append((args, kwargs)),
    )
    monkeypatch.setattr(controller, "show_error", lambda title, message: errors.append((title, message)))

    controller.install_update(str(installer), expected_digest)

    assert launched == []
    assert errors and "数字签名验证失败" in errors[0][1]
    controller.floating_status.deleteLater()
    app.processEvents()


def test_update_download_signal_carries_release_digest_not_replaced_file(tmp_path, monkeypatch):
    """The worker must forward the verified release digest, never re-hash a raced file."""
    release_payload = b"release-verified installer"
    replacement_payload = b"attacker replacement after verification"
    release_digest = hashlib.sha256(release_payload).hexdigest()
    destination = tmp_path / "ScreenTranslator-Lite-setup.exe"

    class FakeUpdateService:
        def __init__(self, repository):
            assert repository == "nimbus-translate/screen-translator"

        def download_verified_update(self, info, target, *, progress_callback, cancel_check):
            assert info is info_marker
            assert not cancel_check.is_set()
            # Model a replacement in the small interval after the service's
            # successful release-side verification and before Signal emission.
            Path(target).write_bytes(replacement_payload)
            progress_callback(len(release_payload), len(release_payload))
            return VerifiedDownload(Path(target), release_digest)

    info_marker = object()
    monkeypatch.setattr(update_tasks_module, "UpdateService", FakeUpdateService)
    task = UpdateDownloadTask(info_marker, destination, "nimbus-translate/screen-translator")
    completed = []
    task.completed.connect(lambda path, digest: completed.append((path, digest)))

    task.run()

    assert destination.read_bytes() == replacement_payload
    assert completed == [(str(destination), release_digest)]


def test_default_ocr_fallback_uses_none_only_after_windows_and_paddle_fail(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("SCREEN_TRANSLATOR_CONFIG", str(tmp_path / "config.json"))
    controller = Application(app)
    controller.config = AppConfig(tmp_path / "config.json")
    attempts = []
    sentinel = object()

    def create(name, *_args):
        attempts.append(name)
        if name != "none":
            raise OCRUnavailableError(f"{name} unavailable")
        return sentinel

    monkeypatch.setattr(application_module, "create_ocr_engine", create)

    assert controller._create_configured_ocr_engine() is sentinel
    assert attempts == ["windows", "paddle", "none"]
    assert controller._active_ocr_engine_name == "none"
    controller.floating_status.deleteLater()
    app.processEvents()


def test_requested_paddle_falls_back_to_windows_when_component_is_unavailable(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("SCREEN_TRANSLATOR_CONFIG", str(tmp_path / "config.json"))
    controller = Application(app)
    controller.config = AppConfig(tmp_path / "config.json")
    controller.config.set("ocr.engine", "paddle")
    attempts = []
    sentinel = object()

    def create(name, *_args):
        attempts.append(name)
        if name == "paddle":
            raise OCRUnavailableError("Paddle component unavailable")
        if name == "windows":
            return sentinel
        raise AssertionError("none must not be used while Windows OCR works")

    monkeypatch.setattr(application_module, "create_ocr_engine", create)

    assert controller._create_configured_ocr_engine() is sentinel
    assert attempts == ["paddle", "windows"]
    assert controller._active_ocr_engine_name == "windows"
    controller.floating_status.deleteLater()
    app.processEvents()


def test_requested_paddle_configuration_error_still_falls_back_to_windows(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("SCREEN_TRANSLATOR_CONFIG", str(tmp_path / "config.json"))
    controller = Application(app)
    controller.config = AppConfig(tmp_path / "config.json")
    controller.config.set("ocr.engine", "paddle")
    attempts = []
    sentinel = object()

    def create(name, *_args):
        attempts.append(name)
        if name == "paddle":
            raise ValueError("component manifest must use HTTPS")
        if name == "windows":
            return sentinel
        raise AssertionError("none must not be used while Windows OCR works")

    monkeypatch.setattr(application_module, "create_ocr_engine", create)

    assert controller._create_configured_ocr_engine() is sentinel
    assert attempts == ["paddle", "windows"]
    assert controller.config.get("ocr.engine") == "windows"
    controller.floating_status.deleteLater()
    app.processEvents()


def test_external_paddle_worker_is_not_warmed_from_untracked_daemon(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("SCREEN_TRANSLATOR_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.delenv("SCREEN_TRANSLATOR_NO_WARMUP", raising=False)
    monkeypatch.delenv("SCREEN_TRANSLATOR_SELFTEST", raising=False)
    controller = Application(app)
    warmed = []

    class ExternalEngine:
        uses_external_component = True

        def warmup(self):
            warmed.append(True)

    controller.ocr_engine = ExternalEngine()
    controller._warmup()

    assert warmed == []
    controller.floating_status.deleteLater()
    app.processEvents()


def test_window_movement_reconfirms_once_then_schedules_capture(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("SCREEN_TRANSLATOR_CONFIG", str(tmp_path / "config.json"))
    controller = Application(app)
    controller._capture_session = _CaptureSession("window", False, False, False, 0x1234)
    controller._highlight_window_rect = (0, 0, 100, 100)
    controller._window_highlight_retries = 0
    controller.monitor_map = []
    reconfirmed = []
    scheduled = []
    monkeypatch.setattr(controller, "_dispose_window_highlight", lambda: None)
    monkeypatch.setattr(
        application_module,
        "get_window_rect_physical",
        lambda _hwnd: (10, 10, 110, 110),
    )
    monkeypatch.setattr(controller, "_after_window_departure", lambda: reconfirmed.append(True))
    monkeypatch.setattr(
        controller,
        "_schedule_capture",
        lambda *args, **kwargs: scheduled.append((args, kwargs)),
    )

    controller._on_window_highlight_finished()
    assert reconfirmed == [True]
    assert scheduled == []
    assert controller._window_highlight_retries == 1

    controller._on_window_highlight_finished()
    assert reconfirmed == [True]
    assert scheduled and scheduled[0][0] == ("window",)
    assert scheduled[0][1]["target_hwnd"] == 0x1234
    controller.floating_status.hide_immediate()
    controller.floating_status.deleteLater()
    app.processEvents()
