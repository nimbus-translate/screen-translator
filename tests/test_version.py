"""Runtime release-version injection regressions."""

from __future__ import annotations

from pathlib import Path

import app.version as version_module


def test_source_version_is_v025_release():
    assert version_module._SOURCE_VERSION == "0.2.5"


def test_frozen_runtime_uses_injected_build_version(tmp_path, monkeypatch):
    (tmp_path / "build-version.txt").write_text("0.2.7", encoding="ascii")
    monkeypatch.setattr(version_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(version_module.sys, "_MEIPASS", str(tmp_path), raising=False)

    assert version_module._runtime_version() == "0.2.7"


def test_frozen_runtime_rejects_malformed_injected_build_version(tmp_path, monkeypatch):
    (tmp_path / "build-version.txt").write_text("totally-not-a-version", encoding="ascii")
    monkeypatch.setattr(version_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(version_module.sys, "_MEIPASS", str(tmp_path), raising=False)

    assert version_module._runtime_version() == version_module._SOURCE_VERSION


def test_lite_build_spec_injects_release_version_file():
    spec = (Path(__file__).parents[1] / "build-lite.spec").read_text(encoding="utf-8")

    assert "SCREEN_TRANSLATOR_BUILD_VERSION" in spec
    assert 'version_file = metadata_dir / "build-version.txt"' in spec
    assert "(str(version_file), \".\")" in spec
