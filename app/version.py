"""Application version and release-channel constants."""

from __future__ import annotations

from pathlib import Path
import re
import sys


_SOURCE_VERSION = "0.2.5-beta"


def _runtime_version() -> str:
    if not getattr(sys, "frozen", False):
        return _SOURCE_VERSION
    try:
        value = (Path(sys._MEIPASS) / "build-version.txt").read_text(
            encoding="ascii"
        ).strip()
    except (AttributeError, OSError):
        return _SOURCE_VERSION
    if re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", value):
        return value
    return _SOURCE_VERSION


__version__ = _runtime_version()
RELEASE_REPOSITORY = "nimbus-translate/screen-translator"
RELEASE_CHANNEL = "beta"
