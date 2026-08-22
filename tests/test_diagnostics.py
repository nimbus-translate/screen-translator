"""Diagnostic bundle safety tests."""

from __future__ import annotations

import json
import zipfile

import pytest

from app.config import AppConfig
from services.diagnostics import DiagnosticsExportError, DiagnosticsExporter


def _exporter(config, logs, **kwargs):
    return DiagnosticsExporter(
        config,
        logs_directory=logs,
        app_version="test-version",
        display_provider=lambda: [{"name": "Test display"}],
        ocr_backends_provider=lambda: [{"name": "windows", "available": True}],
        **kwargs,
    )


def test_export_redacts_config_and_logs(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "translation": {
                    "openai": {"api_key": "sk-live-config-secret-123456"},
                    "custom_token": "unwanted-secret",
                }
            }
        ),
        encoding="utf-8",
    )
    config = AppConfig(config_path)
    logs = tmp_path / "logs"
    logs.mkdir()
    logs.joinpath("app.log").write_text(
        '\n'.join(
            (
                'request api_key="sk-live-log-secret-123456" bearer abcdefghijk',
                '{"api_key":"json-secret-123456", "access_token":"access-secret-123456"}',
                'GET https://example.test/translate?q=hello&key=query-secret-123456',
                "单条翻译失败，保留原文：'用户的私密屏幕原文'",
                "批量翻译第 2 次失败：HTTPSConnectionPool(url='/translate?q=PRIVATE_OCR_TEXT_7f91&key=old-key')",
                "批量翻译第 3 次异常：https://example.test/?query=PRIVATE_QUERY_TEXT_8a02",
                "状态：处理失败：Google 请求失败 https://example.test/?q=PRIVATE_STATUS_TEXT_9b13",
                "TranslationError: https://example.test/?q=PRIVATE_TRACEBACK_TEXT_ac24&client=gtx",
                "GET https://example.test/?input=PRIVATE_INPUT_TEXT_bd35&source=PRIVATE_SOURCE_TEXT_ce46",
                "状态：Google 返回错误 500：PRIVATE_RESPONSE_BODY_df57",
            )
        ),
        encoding="utf-8",
    )

    bundle = _exporter(config, logs).export(tmp_path / "support.zip")

    with zipfile.ZipFile(bundle) as archive:
        snapshot = json.loads(archive.read("config.json"))
        joined = "\n".join(archive.read(name).decode("utf-8") for name in archive.namelist())
    assert snapshot["translation"]["openai"]["api_key"] == "***"
    assert snapshot["translation"]["custom_token"] == "***"
    assert "sk-live-config-secret-123456" not in joined
    assert "sk-live-log-secret-123456" not in joined
    assert "bearer abcdefghijk" not in joined.lower()
    assert "json-secret-123456" not in joined
    assert "access-secret-123456" not in joined
    assert "query-secret-123456" not in joined
    assert "用户的私密屏幕原文" not in joined
    assert "PRIVATE_OCR_TEXT_7f91" not in joined
    assert "PRIVATE_QUERY_TEXT_8a02" not in joined
    assert "PRIVATE_STATUS_TEXT_9b13" not in joined
    assert "PRIVATE_TRACEBACK_TEXT_ac24" not in joined
    assert "PRIVATE_INPUT_TEXT_bd35" not in joined
    assert "PRIVATE_SOURCE_TEXT_ce46" not in joined
    assert "PRIVATE_RESPONSE_BODY_df57" not in joined


def test_export_uses_strict_archive_whitelist(tmp_path):
    config = AppConfig(tmp_path / "config.json")
    logs = tmp_path / "logs"
    logs.mkdir()
    for filename in ("app.log", "app.log.1", "app.log.3", "app.log.4", "capture.png", "regions.json", "model.bin"):
        logs.joinpath(filename).write_text(filename, encoding="utf-8")

    bundle = _exporter(config, logs).export(tmp_path / "support")

    with zipfile.ZipFile(bundle) as archive:
        assert set(archive.namelist()) == {
            "config.json",
            "system.json",
            "logs/app.log",
            "logs/app.log.1",
            "logs/app.log.3",
        }
        info = json.loads(archive.read("system.json"))
    assert info["app_version"] == "test-version"
    assert info["displays"] == [{"name": "Test display"}]
    assert info["ocr_backends"] == [{"name": "windows", "available": True}]


def test_export_failure_removes_temp_and_preserves_existing_target(tmp_path, monkeypatch):
    config = AppConfig(tmp_path / "config.json")
    logs = tmp_path / "logs"
    logs.mkdir()
    target = tmp_path / "support.zip"
    target.write_bytes(b"known-good-old-bundle")
    exporter = _exporter(config, logs)

    def fail(_temporary):
        raise OSError("disk full")

    monkeypatch.setattr(exporter, "_write_archive", fail)
    with pytest.raises(DiagnosticsExportError):
        exporter.export(target)

    assert target.read_bytes() == b"known-good-old-bundle"
    assert list(tmp_path.glob(".support.*.tmp")) == []
