"""Background installer for the optional PaddleOCR component."""

from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal

from services.ocr.component_manager import PaddleComponentManager


class PaddleComponentInstallTask(QThread):
    progress = Signal(int, int)
    completed = Signal(str)
    failed = Signal(str)

    def __init__(self, manager: PaddleComponentManager, parent=None) -> None:
        super().__init__(parent)
        self.manager = manager
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            entrypoint = self.manager.ensure_installed(
                progress_callback=lambda done, total: self.progress.emit(
                    int(done), int(total or 0)
                ),
                cancel_check=self._cancel,
            )
        except Exception as exc:
            if not self._cancel.is_set():
                self.failed.emit(str(exc))
            return
        if not self._cancel.is_set():
            self.completed.emit(str(entrypoint))
