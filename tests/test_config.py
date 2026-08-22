"""配置文件读写与默认值合并测试。"""

from app.config import AppConfig


def test_defaults_loaded(tmp_path):
    config = AppConfig(tmp_path / "config.json")
    assert config.get("ocr.min_confidence") == 0.6
    assert config.get("ocr.engine") == "windows"
    assert config.get("ocr.lang") == "auto"
    assert config.get("updates.auto_check") is True
    assert config.get("translation.service") == "google_free"
    assert config.hotkeys()["capture_region"] == "ctrl+shift+a"
    assert config.get("appearance.palette") == "warm_paper"
    assert config.get("appearance.accent") == "#2878E8"
    assert config.get("appearance.motion_profile") == "flow"


def test_merge_partial(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"ocr": {"min_confidence": 0.8}}', encoding="utf-8")
    config = AppConfig(path)
    assert config.get("ocr.min_confidence") == 0.8
    assert config.get("ocr.engine") == "windows"
    assert config.get("ocr.lang") == "auto"
    assert config.get("appearance.density") == "balanced"


def test_legacy_google_free_speed_settings_are_normalized(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        '{"translation":{"google_free_max_workers":8,'
        '"google_free_interval":0,"google_free_partial_retries":2}}',
        encoding="utf-8",
    )

    config = AppConfig(path)

    assert config.get("translation.google_free_max_workers") == 4
    assert config.get("translation.google_free_interval") == 0.05
    assert config.get("translation.google_free_partial_retries") == 0
    assert config.get("translation.google_free_fallback_to_mymemory") is True


def test_save_load_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    config = AppConfig(path)
    config.set("overlay.font_size", 24)
    config.set("hotkeys.capture_region", "ctrl+alt+x")
    config.set("appearance.palette", "midnight")
    config.set("appearance.accent", "#7258D6")
    config.save()
    config2 = AppConfig(path)
    assert config2.get("overlay.font_size") == 24
    assert config2.hotkeys()["capture_region"] == "ctrl+alt+x"
    assert config2.get("appearance.palette") == "midnight"
    assert config2.get("appearance.accent") == "#7258D6"


def test_invalid_appearance_values_fall_back_without_touching_other_sections(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        '{"appearance":{"palette":"laser","accent":"red/blue",'
        '"motion_profile":"warp","density":"tiny","surface":"glass",'
        '"reduce_motion":"no"},"ocr":{"min_confidence":0.77}}',
        encoding="utf-8",
    )

    config = AppConfig(path)

    assert config.get("appearance.palette") == "warm_paper"
    assert config.get("appearance.accent") == "#2878E8"
    assert config.get("appearance.motion_profile") == "flow"
    assert config.get("appearance.density") == "balanced"
    assert config.get("appearance.surface") == "layered"
    assert config.get("appearance.reduce_motion") is False
    assert config.get("ocr.min_confidence") == 0.77


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
