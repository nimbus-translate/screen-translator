"""Regression tests for the appearance settings and their local preview."""

from __future__ import annotations

import copy
import hashlib
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible, QColor, QImage, QPixmap
from PySide6.QtWidgets import QApplication

from app.config import DEFAULTS, AppConfig
from ui.appearance import current_appearance, resolve_tokens
from ui.main_window import MainWindow
from ui.motion import BASE, configure_motion, continuous_motion_enabled, motion_duration
from ui.settings_dialog import SettingsDialog
from ui.style import apply_style


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    previous = current_appearance()
    apply_style(app)
    yield app
    apply_style(app, previous)


class _Controller:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


def test_personalization_preview_is_live_but_does_not_mutate_config(qapp, tmp_path):
    config = AppConfig(tmp_path / "config.json")
    before = copy.deepcopy(config.data)
    dialog = SettingsDialog(config)
    transition = dialog.personalization_preview._transition_animation
    reveal = dialog.personalization_preview._reveal_animation

    assert [button.text() for button in dialog.nav_buttons][1] == "个性化"
    assert all(card.accessibleName() for card in dialog.palette_choices.cards)
    assert dialog.palette_choices.cards[0].accessibleDescription().endswith("当前已选择")
    assert dialog.palette_choices.cards[0].isCheckable()
    assert QAccessible.queryAccessibleInterface(
        dialog.palette_choices.cards[0]
    ).role() == QAccessible.Role.RadioButton
    dialog.palette_choices.set_value("midnight")
    dialog.accent_choices.set_value("#7258D6")
    dialog.motion_choices.set_value("calm")
    dialog.density_choices.set_value("compact")
    dialog.surface_choices.set_value("clean")
    qapp.processEvents()

    state = dialog.personalization_preview.state()
    assert state["palette"] == "midnight"
    assert state["accent"] == "#7258D6"
    assert state["motion_profile"] == "calm"
    assert state["density"] == "compact"
    assert state["surface"] == "clean"
    assert config.data == before
    assert dialog.personalization_preview._transition_animation is transition
    assert dialog.personalization_preview._reveal_animation is reveal
    assert dialog.personalization_hint.property("dirty") is True
    dialog.deleteLater()


def test_custom_accent_is_preserved_when_saving_other_settings(qapp, tmp_path):
    path = tmp_path / "config.json"
    config = AppConfig(path)
    config.set("appearance.accent", "#336699")
    dialog = SettingsDialog(config)

    assert dialog.accent_choices.value() == "#336699"
    assert any(card.label == "自定义" for card in dialog.accent_choices.cards)
    dialog.chk_tray.setChecked(not dialog.chk_tray.isChecked())
    hotkeys = dialog._validated_hotkeys()
    assert hotkeys is not None
    dialog._commit_accept(hotkeys)
    dialog._save_close_timer.stop()

    assert AppConfig(path).get("appearance.accent") == "#336699"
    dialog.deleteLater()


def test_system_palette_and_rapid_preview_switch_keep_visual_continuity(qapp, tmp_path):
    system_tokens = resolve_tokens({"palette": "system"})
    assert system_tokens.palette == "system"
    assert system_tokens.root in {
        resolve_tokens({"palette": "warm_paper"}).root,
        resolve_tokens({"palette": "midnight"}).root,
    }

    dialog = SettingsDialog(AppConfig(tmp_path / "config.json"))
    preview = dialog.personalization_preview
    first = copy.deepcopy(DEFAULTS["appearance"])
    first["palette"] = "midnight"
    preview.set_options(first, animate=True)
    preview._set_transition_progress(0.37)
    color_before_switch = preview._color("root").name(QColor.NameFormat.HexRgb)

    second = copy.deepcopy(DEFAULTS["appearance"])
    second["palette"] = "mist"
    preview.set_options(second, animate=True)
    color_after_switch = preview._color("root").name(QColor.NameFormat.HexRgb)

    assert color_after_switch == color_before_switch
    preview._transition_animation.stop()
    preview._reveal_animation.stop()
    dialog.deleteLater()


def test_restore_defaults_only_resets_appearance_and_save_round_trips(qapp, tmp_path):
    path = tmp_path / "config.json"
    config = AppConfig(path)
    config.set("ocr.min_confidence", 0.83)
    config.set("translation.openai.api_key", "keep-this-key")
    dialog = SettingsDialog(config)

    dialog.palette_choices.set_value("midnight")
    dialog.accent_choices.set_value("#7258D6")
    dialog._reset_personalization()
    assert dialog._personalization_state() == DEFAULTS["appearance"]
    assert config.get("ocr.min_confidence") == 0.83
    assert config.get("translation.openai.api_key") == "keep-this-key"

    dialog.palette_choices.set_value("mist")
    dialog.accent_choices.set_value("#168A82")
    dialog.motion_choices.set_value("minimal")
    dialog.density_choices.set_value("spacious")
    dialog.surface_choices.set_value("clean")
    dialog.chk_reduce_motion.setChecked(True)
    hotkeys = dialog._validated_hotkeys()
    assert hotkeys is not None
    dialog._commit_accept(hotkeys)
    dialog._save_close_timer.stop()

    loaded = AppConfig(path)
    assert loaded.get("appearance.palette") == "mist"
    assert loaded.get("appearance.accent") == "#168A82"
    assert loaded.get("appearance.motion_profile") == "minimal"
    assert loaded.get("appearance.density") == "spacious"
    assert loaded.get("appearance.surface") == "clean"
    assert loaded.get("appearance.reduce_motion") is True
    assert loaded.get("ocr.min_confidence") == 0.83
    assert loaded.get("translation.openai.api_key") == "keep-this-key"
    dialog.deleteLater()


def test_personalization_preview_renders_selected_theme_and_accent(qapp, tmp_path):
    dialog = SettingsDialog(AppConfig(tmp_path / "config.json"))
    preview = dialog.personalization_preview
    state = copy.deepcopy(DEFAULTS["appearance"])
    state.update({"palette": "midnight", "accent": "#7258D6"})
    preview.set_options(state, animate=False)
    preview.resize(680, 188)
    image = QImage(preview.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    preview.render(image)
    tokens = resolve_tokens(state)

    def near(actual: QColor, expected: str, tolerance: int = 4) -> bool:
        target = QColor(expected)
        return all(
            abs(a - b) <= tolerance
            for a, b in zip(actual.getRgb()[:3], target.getRgb()[:3])
        )

    root_pixels = 0
    surface_pixels = 0
    accent_pixels = 0
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            root_pixels += near(color, tokens.root)
            surface_pixels += near(color, tokens.surface)
            accent_pixels += near(color, tokens.accent)

    assert root_pixels > 1000
    assert surface_pixels > 3000
    assert accent_pixels > 30
    dialog.deleteLater()


def test_settings_glyph_uses_the_canonical_brand_asset_pixels(qapp, tmp_path):
    dialog = SettingsDialog(AppConfig(tmp_path / "config.json"))
    glyph = dialog._settings_glyph
    source_path = glyph._icon_source

    assert source_path.name == "app_launch_v4.png"
    assert source_path.exists()
    assert hashlib.sha256(source_path.read_bytes()).hexdigest().upper() == (
        "428AD60030CDFF6ED0035005D2424E174943392B05D37D79646D906CA196AD8D"
    )

    source = QPixmap(str(source_path))
    assert not source.isNull()
    assert source.size().width() == 1024
    assert source.size().height() == 1024
    expected = source.scaled(
        glyph.size(),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    actual = glyph.pixmap()
    assert actual is not None
    assert not actual.isNull()
    assert actual.toImage() == expected.toImage()
    dialog.deleteLater()


def test_density_changes_real_main_window_metrics(qapp, tmp_path):
    config = AppConfig(tmp_path / "config.json")
    window = MainWindow(_Controller(config))

    apply_style(qapp, {**DEFAULTS["appearance"], "density": "compact"})
    window.refresh_appearance()
    compact_height = window.combo_source.minimumHeight()
    compact_spacing = window._home_layout.spacing()

    apply_style(qapp, {**DEFAULTS["appearance"], "density": "spacious"})
    window.refresh_appearance()
    assert window.combo_source.minimumHeight() > compact_height
    assert window._home_layout.spacing() > compact_spacing
    window.deleteLater()


def test_motion_profiles_scale_duration_and_reduced_wins(monkeypatch):
    # Offscreen Qt reports UI effects as disabled, so use the explicit full
    # override while checking profile scaling.
    monkeypatch.setenv("SCREEN_TRANSLATOR_MOTION", "full")
    configure_motion("flow", False)
    assert motion_duration(BASE) == BASE
    configure_motion("calm", False)
    assert motion_duration(BASE) > BASE
    configure_motion("minimal", False)
    assert motion_duration(BASE) < BASE
    assert not continuous_motion_enabled()
    monkeypatch.setenv("SCREEN_TRANSLATOR_MOTION", "reduced")
    configure_motion("flow", False)
    assert motion_duration(BASE) == 0
    configure_motion("flow", False)
