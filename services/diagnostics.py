"""Safe, support-ready diagnostic bundle export.

The exporter deliberately builds an allowlisted archive instead of walking the
application-data directory.  Captures, translation history and OCR models are
therefore never candidates for inclusion.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import re
import sys
import tempfile
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from app.config import AppConfig
from app.logger import log_dir, redact


_ARCHIVE_CONFIG = "config.json"
_ARCHIVE_SYSTEM = "system.json"
_LOG_NAME_RE = re.compile(r"^app\.log(?:\.[1-3])?$")
_LEGACY_TRANSLATION_TEXT_RE = re.compile(
    r"(单条翻译失败，保留原文：)[^\r\n]*"
)
_LEGACY_BATCH_TRANSLATION_ERROR_RE = re.compile(
    r"(批量翻译第\s*\d+\s*次(?:失败|异常)[：:])[^\r\n]*"
)
_LEGACY_PIPELINE_ERROR_RE = re.compile(
    r"((?:状态[：:]\s*)?处理失败[：:])[^\r\n]*"
)
_LEGACY_PROVIDER_ERROR_RE = re.compile(
    r"(状态[：:][^\r\n]{0,96}?(?:请求失败|返回错误)[^：:\r\n]{0,24}[：:])[^\r\n]*"
)
_LEGACY_QUERY_VALUE_RE = re.compile(
    r"(?i)([?&](?:q|text|query|input|source)=)[^&\s'\"<>]*"
)
_SENSITIVE_KEY_RE = re.compile(
    r"(?:api[_-]?key|apikey|secret|token|authorization|password)", re.IGNORECASE
)


class DiagnosticsExportError(RuntimeError):
    """Raised when a diagnostic archive could not be safely produced."""


def _json_bytes(value: Any) -> bytes:
    """Serialize through the logger redactor as a final guardrail."""
    rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    return redact(rendered).encode("utf-8")


def _redact_snapshot(value: Any) -> Any:
    """Mask unknown secret-shaped fields in addition to AppConfig's snapshot."""
    if isinstance(value, Mapping):
        return {
            str(key): "***" if _SENSITIVE_KEY_RE.search(str(key)) else _redact_snapshot(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_snapshot(item) for item in value]
    return value


def _sanitize_log(content: str) -> str:
    """Redact credentials and captured text emitted by older app versions."""
    content = redact(content)
    content = _LEGACY_TRANSLATION_TEXT_RE.sub(r"\1***", content)
    content = _LEGACY_BATCH_TRANSLATION_ERROR_RE.sub(r"\1***", content)
    content = _LEGACY_PIPELINE_ERROR_RE.sub(r"\1***", content)
    content = _LEGACY_PROVIDER_ERROR_RE.sub(r"\1***", content)
    return _LEGACY_QUERY_VALUE_RE.sub(r"\1***", content)


def _default_version() -> str:
    for distribution in ("screen-translator", "ScreenTranslator"):
        try:
            return importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            continue
    return "development"


def _default_display_info() -> list[dict[str, Any]]:
    """Read Qt's existing display list without creating a GUI application."""
    try:
        from PySide6.QtGui import QGuiApplication

        app = QGuiApplication.instance()
        if app is None:
            return []
        return [_screen_info(screen) for screen in app.screens()]
    except Exception:
        return []


def _rect_info(rect: Any) -> dict[str, int]:
    return {"x": int(rect.x()), "y": int(rect.y()), "width": int(rect.width()), "height": int(rect.height())}


def _screen_info(screen: Any) -> dict[str, Any]:
    info: dict[str, Any] = {}
    for key, getter in (("name", "name"), ("device_pixel_ratio", "devicePixelRatio"), ("logical_dpi_x", "logicalDotsPerInchX"), ("logical_dpi_y", "logicalDotsPerInchY")):
        try:
            value = getattr(screen, getter)()
            info[key] = float(value) if key != "name" else str(value)
        except Exception:
            pass
    for key, getter in (("geometry", "geometry"), ("available_geometry", "availableGeometry")):
        try:
            info[key] = _rect_info(getattr(screen, getter)())
        except Exception:
            pass
    return info


def _default_ocr_backends() -> list[dict[str, Any]]:
    """Report registered backends and their availability, never initialize models."""
    try:
        # Registration is normally performed by Application.  The diagnostic
        # service also works before the controller is constructed.
        import services.ocr.null_ocr  # noqa: F401
        import services.ocr.paddle_ocr  # noqa: F401
        import services.ocr.windows_ocr  # noqa: F401
        from services.ocr import base

        result = []
        for name, engine in sorted(base._REGISTRY.items()):  # registry is the source of truth
            try:
                available = bool(engine.available())
            except Exception:
                available = False
            result.append({"name": name, "available": available})
        return result
    except Exception:
        return []


def _default_system_info(
    version: str | None = None,
    display_provider: Callable[[], list[dict[str, Any]]] | None = None,
    ocr_backends_provider: Callable[[], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    qt_version = "unavailable"
    pyside_version = "unavailable"
    try:
        from PySide6 import __version__ as pyside_version
        from PySide6.QtCore import qVersion

        qt_version = qVersion()
    except Exception:
        pass

    return {
        "app_version": version or _default_version(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
        },
        "python": {"version": sys.version, "implementation": platform.python_implementation()},
        "qt": {"binding": "PySide6", "binding_version": pyside_version, "version": qt_version},
        "displays": (display_provider or _default_display_info)(),
        "ocr_backends": (ocr_backends_provider or _default_ocr_backends)(),
    }


class DiagnosticsExporter:
    """Export a small redacted ZIP suitable for attaching to a support ticket."""

    def __init__(
        self,
        config: AppConfig,
        *,
        logs_directory: Path | None = None,
        app_version: str | None = None,
        display_provider: Callable[[], list[dict[str, Any]]] | None = None,
        ocr_backends_provider: Callable[[], list[dict[str, Any]]] | None = None,
    ) -> None:
        self.config = config
        self.logs_directory = Path(logs_directory) if logs_directory is not None else log_dir()
        self.app_version = app_version
        self.display_provider = display_provider
        self.ocr_backends_provider = ocr_backends_provider

    def _config_snapshot(self) -> dict[str, Any]:
        return _redact_snapshot(self.config.masked_snapshot())

    def _log_files(self) -> list[Path]:
        """Only the current RotatingFileHandler files may enter the bundle."""
        try:
            candidates = [
                path
                for path in self.logs_directory.iterdir()
                if path.is_file() and not path.is_symlink() and _LOG_NAME_RE.fullmatch(path.name)
            ]
        except OSError:
            return []
        return sorted(candidates, key=lambda path: (path.name != "app.log", path.name))

    def _write_archive(self, temporary_path: Path) -> None:
        with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(_ARCHIVE_CONFIG, _json_bytes(self._config_snapshot()))
            archive.writestr(
                _ARCHIVE_SYSTEM,
                _json_bytes(
                    _default_system_info(
                        self.app_version, self.display_provider, self.ocr_backends_provider
                    )
                ),
            )
            for source in self._log_files():
                # Logs are formatted with redact() already; scrub again to
                # protect bundles made from older log files.
                content = source.read_text(encoding="utf-8", errors="replace")
                archive.writestr(f"logs/{source.name}", _sanitize_log(content))

    def export(self, target_path: str | Path) -> Path:
        """Atomically replace *target_path* with a complete diagnostic ZIP.

        A temporary file is created beside the target, so ``os.replace`` is
        atomic on Windows.  Any exception removes that temporary file and
        leaves a pre-existing export untouched.
        """
        target = Path(target_path)
        if not target.name or target.name in {".", ".."}:
            raise DiagnosticsExportError("诊断包目标路径无效")
        if target.suffix.lower() != ".zip":
            target = target.with_suffix(".zip")
        temporary_name: str | None = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{target.stem}.", suffix=".tmp", dir=target.parent
            )
            os.close(descriptor)
            temporary = Path(temporary_name)
            self._write_archive(temporary)
            os.replace(temporary, target)
            temporary_name = None
            return target
        except Exception as exc:
            raise DiagnosticsExportError(f"导出诊断包失败：{exc}") from exc
        finally:
            if temporary_name:
                try:
                    Path(temporary_name).unlink(missing_ok=True)
                except OSError:
                    pass


def export_diagnostics(target_path: str | Path, config: AppConfig, **kwargs: Any) -> Path:
    """Convenience entry point for the UI action added by a later change."""
    return DiagnosticsExporter(config, **kwargs).export(target_path)
