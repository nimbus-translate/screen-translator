"""Qt thread wrappers for release checks and verified update downloads."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from services.update_service import UpdateInfo, UpdateService


class UpdateCheckTask(QThread):
    updateFound = Signal(object)
    upToDate = Signal()
    failed = Signal(str)

    def __init__(
        self,
        current_version: str,
        repository: str,
        *,
        include_prereleases: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.current_version = current_version
        self.repository = repository
        self.include_prereleases = include_prereleases
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            info = UpdateService(self.repository).check_for_update(
                self.current_version,
                include_prereleases=self.include_prereleases,
                cancel_check=self._cancel,
            )
        except Exception as exc:
            if not self._cancel.is_set():
                self.failed.emit(str(exc))
            return
        if self._cancel.is_set():
            return
        if info is None:
            self.upToDate.emit()
        else:
            self.updateFound.emit(info)


class UpdateDownloadTask(QThread):
    progress = Signal(int, int)
    completed = Signal(str, str)
    failed = Signal(str)

    def __init__(
        self,
        info: UpdateInfo,
        destination: str | Path,
        repository: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.info = info
        self.destination = Path(destination)
        self.repository = repository
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        def report(done: int, total: int | None) -> None:
            self.progress.emit(int(done), int(total or 0))

        try:
            verified = UpdateService(self.repository).download_verified_update(
                self.info,
                self.destination,
                progress_callback=report,
                cancel_check=self._cancel,
            )
        except Exception as exc:
            if not self._cancel.is_set():
                self.failed.emit(str(exc))
            return
        if not self._cancel.is_set():
            # Carry the release-published digest into the install confirmation.
            # The controller hashes the file again immediately before launch.
            self.completed.emit(str(verified.path), verified.sha256)
