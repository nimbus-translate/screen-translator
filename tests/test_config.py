"""配置文件读写与默认值合并测试。"""

from app.config import AppConfig


def test_defaults_loaded(tmp_path):
    config = AppConfig(tmp_path / "config.json")
    assert config.get("ocr.min_confidence") == 0.6
    assert config.get("translation.service") == "google_free"
    assert config.hotkeys()["capture_region"] == "ctrl+shift+a"


def test_merge_partial(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"ocr": {"min_confidence": 0.8}}', encoding="utf-8")
    config = AppConfig(path)
    assert config.get("ocr.min_confidence") == 0.8
    assert config.get("ocr.engine") == "paddle"


def test_save_load_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    config = AppConfig(path)
    config.set("overlay.font_size", 24)
    config.set("hotkeys.capture_region", "ctrl+alt+x")
    config.save()
    config2 = AppConfig(path)
    assert config2.get("overlay.font_size") == 24
    assert config2.hotkeys()["capture_region"] == "ctrl+alt+x"


def test_masked_snapshot_hides_keys(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"translation": {"openai": {"api_key": "sk-secret-1234567890"}}}', encoding="utf-8")
    config = AppConfig(path)
    snapshot = config.masked_snapshot()
    assert snapshot["translation"]["openai"]["api_key"] == "***"
    assert "sk-secret-1234567890" not in str(snapshot)


def test_corrupt_config_backed_up(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{nope", encoding="utf-8")
    config = AppConfig(path)
    assert config.get("ocr.min_confidence") == 0.6
    assert path.with_suffix(".json.bak").exists()
