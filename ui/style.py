"""ScreenTranslator 的统一暖白极简视觉系统。"""

from __future__ import annotations

import re
from typing import Any

from ui.appearance import AppearanceTokens, set_current_appearance
from ui.motion import configure_motion


MAIN_WINDOW_QSS = """
QWidget#Root, QWidget#HomePage { background: #F7F5F1; }
QLabel#TitleLabel { color: #252826; font-size: 24px; font-weight: 700; }
QLabel#HintLabel { color: #76756F; font-size: 13px; }
QLabel#SectionLabel { color: #6E6D67; font-size: 14px; }
QLabel#StatusLabel { color: #565852; font-size: 13px; }
QLabel#AppMark {
    background: #FFFEFC; color: #252826; border: 1px solid #DEDCD5;
    border-radius: 12px; font-size: 21px; font-weight: 600;
}
QWidget#ActionPanel, QWidget#OptionsPanel, QWidget#FooterPanel {
    background: #FFFEFC; border: 1px solid #E1DFD9; border-radius: 14px;
}
QPushButton {
    background: #FFFEFC; color: #313330; border: 1px solid #DCDAD3;
    border-radius: 10px; padding: 8px 14px; font-size: 13px; font-weight: 500;
}
QPushButton:hover { background: #FAF9F6; border-color: #BFC3BD; }
QPushButton:pressed { background: #F0EFEB; }
QPushButton:disabled { background: #F5F4F1; color: #B8B8B1; border-color: #E8E6E0; }
QPushButton#PrimaryButton {
    background: #2878E8; color: #FFFFFF; border: 1px solid #2878E8;
    border-radius: 12px; padding: 12px 16px; font-size: 15px; font-weight: 600;
}
QPushButton#PrimaryButton:hover { background: #1E6EDC; border-color: #1E6EDC; }
QPushButton#PrimaryButton:pressed { background: #165FC5; }
QPushButton#ActionButton {
    background: #FFFEFC; color: #2E312F; border: 1px solid #DCDAD3;
    border-radius: 12px; padding: 12px 16px; font-size: 15px; font-weight: 600;
}
QPushButton#SettingsButton { font-size: 15px; }
QPushButton#RefreshButton { color: #216FD6; border-color: #80B4F3; }
QPushButton#RefreshButton:hover { background: #EDF5FF; border-color: #2878E8; }
QPushButton#ActionButton:hover { background: #F8FAFD; border-color: #8CB8F4; color: #2878E8; }
QPushButton#ToggleButton:checked {
    background: #EDF5FF; color: #216FD6; border-color: #80B3F3;
}
QComboBox {
    min-height: 28px; background: #FFFEFC; color: #30322F;
    border: 1px solid #DCDAD3; border-radius: 9px; padding: 5px 28px 5px 10px;
    font-size: 13px;
}
QComboBox:hover, QComboBox:focus { border-color: #86B4F0; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox QAbstractItemView {
    background: #FFFEFC; border: 1px solid #DCDAD3; border-radius: 8px;
    selection-background-color: #EDF5FF; selection-color: #216FD6; padding: 4px; outline: none;
}
QCheckBox { color: #4F514D; font-size: 13px; spacing: 7px; }
QCheckBox::indicator {
    width: 17px; height: 17px; background: #FFFEFC; border: 1.5px solid #BFC3BD; border-radius: 5px;
}
QCheckBox::indicator:hover { border-color: #2878E8; }
QCheckBox::indicator:checked { background: #2878E8; border-color: #2878E8; }
QToolTip { background: #252826; color: #FFFFFF; border: none; border-radius: 7px; padding: 6px 9px; }
QMenu { background: #FFFEFC; border: 1px solid #DEDCD5; border-radius: 10px; padding: 6px; }
QMenu::item { padding: 8px 26px 8px 11px; border-radius: 7px; color: #30322F; }
QMenu::item:selected { background: #EDF5FF; color: #216FD6; }
QMenu::separator { height: 1px; background: #E8E6E0; margin: 5px 7px; }
"""

DIALOG_QSS = """
QDialog, QWidget#SettingsDialog { background: #F7F5F1; }
QWidget#SettingsDialog QLabel#SettingsTitle { color: #292D2A; font-size: 24px; font-weight: 700; }
QWidget#SettingsDialog QLabel#SettingsSubtitle { color: #77766F; font-size: 13px; }
QFrame#SettingsShell { background: #FFFEFC; border: 1px solid #E2DFD8; border-radius: 16px; }
QFrame#SettingsDivider { background: #E9E6E0; max-width: 1px; border: none; }
QFrame#SettingsPageHost { background: transparent; border: none; }
QStackedWidget#SettingsPages { background: transparent; border: none; }
QFrame#SettingsNavIndicator { background: #2878E8; border: none; border-radius: 2px; }
QFrame#SettingsPageCurtain {
    background: #F3F8FF;
    border: none;
    border-left: 4px solid #2878E8;
}
QScrollArea, QScrollArea > QWidget > QWidget { background: #FFFEFC; border: none; }
QScrollArea::viewport { background: #FFFEFC; }
QScrollBar:vertical { width: 0px; background: transparent; }
QFrame#SettingsCard {
    background: #FFFEFC; border: 1px solid #E4E1DA; border-radius: 13px;
}
QFrame#SettingsCard:hover { border-color: #C8DAF3; background: #FDFEFF; }
QLabel#SettingsCardTitle { color: #2B2F2C; font-size: 15px; font-weight: 700; }
QLabel#SettingsCardDescription { color: #7A7973; font-size: 12px; }
QLabel#PersonalizationHint { color: #77766F; font-size: 12px; }
QLabel#PersonalizationHint[dirty="true"] { color: #216FD6; }
QPushButton#SettingsNavButton {
    text-align: left; color: #5D5E59; background: transparent; border: 1px solid transparent;
    border-radius: 10px; padding: 0 15px; font-size: 14px; font-weight: 500;
}
QPushButton#SettingsNavButton:hover { background: #F8F7F3; color: #343734; }
QPushButton#SettingsNavButton:checked {
    background: #F2F7FF; color: #2878E8; border-color: #D7E8FF; font-weight: 600;
}
QPushButton#SettingsNavButton:pressed { background: #E5F0FF; color: #165FC5; }
QPushButton#DialogCancelButton { min-width: 116px; background: #FFFEFC; border-color: #DCDAD3; }
QPushButton#DialogSaveButton {
    min-width: 116px; background: #2878E8; color: #FFFFFF; border: 1px solid #2878E8;
    border-radius: 10px; font-weight: 600;
}
QPushButton#DialogSaveButton:hover { background: #1E6EDC; border-color: #1E6EDC; }
QPushButton#DialogSaveButton:pressed { background: #165FC5; border-color: #165FC5; }
QPushButton#DialogSaveButton[saved="true"] {
    background: #F2F7FF; color: #216FD6; border-color: #80B4F3;
}
QPushButton#PersonalizationResetButton {
    background: transparent; color: #5D5E59; border-color: #DCDAD3;
}
QPushButton#PersonalizationResetButton:hover { color: #2878E8; border-color: #80B4F3; }
QLabel#NoticeLabel {
    color: #565852; background: #F4F8FF; border: 1px solid #D7E8FF;
    border-radius: 8px; padding: 9px;
}
QTabWidget::pane { border: 1px solid #E1DFD9; border-radius: 12px; background: #FFFEFC; top: -1px; }
QTabBar::tab {
    background: transparent; color: #77766F; padding: 9px 16px; margin-right: 3px;
    border-top-left-radius: 8px; border-top-right-radius: 8px; font-size: 13px;
}
QTabBar::tab:hover { color: #3B3D39; background: #F1F0EC; }
QTabBar::tab:selected { color: #216FD6; background: #EDF5FF; font-weight: 600; }
QLabel { color: #4F514D; }
QLineEdit, QSpinBox, QDoubleSpinBox, QKeySequenceEdit {
    min-height: 26px; background: #FFFEFC; color: #30322F;
    border: 1px solid #DCDAD3; border-radius: 8px; padding: 5px 9px; font-size: 13px;
}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    width: 0px; border: none;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QKeySequenceEdit:focus { border-color: #86B4F0; }
QSlider::groove:horizontal { height: 4px; border-radius: 2px; background: #E1DFD9; }
QSlider::sub-page:horizontal { border-radius: 2px; background: #2878E8; }
QSlider::handle:horizontal { width: 15px; height: 15px; margin: -5px 0; border-radius: 7px; background: #2878E8; }
QGroupBox { border: 1px solid #E1DFD9; border-radius: 11px; margin-top: 12px; padding: 14px 12px 10px; color: #30322F; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
QDialogButtonBox QPushButton { min-width: 86px; }
QColorDialog, QFileDialog { background: #F7F5F1; }
QMessageBox { background: #F7F5F1; }
"""


def _replace_tokens(source: str, tokens: AppearanceTokens) -> str:
    replacements = {
        "#F7F5F1": tokens.root,
        "#FFFEFC": tokens.surface,
        "#FBFAF7": tokens.surface_alt,
        "#FAF9F6": tokens.surface_hover,
        "#F8F7F3": tokens.surface_hover,
        "#F5F4F1": tokens.surface_alt,
        "#F1F0EC": tokens.surface_alt,
        "#F0EFEB": tokens.surface_alt,
        "#F8FAFD": tokens.surface_hover,
        "#FDFEFF": tokens.surface_hover,
        "#252826": tokens.ink,
        "#292D2A": tokens.ink,
        "#2B2F2C": tokens.ink,
        "#30322F": tokens.ink,
        "#313330": tokens.ink,
        "#2E312F": tokens.ink,
        "#343734": tokens.ink_soft,
        "#3B3D39": tokens.ink_soft,
        "#4F514D": tokens.ink_soft,
        "#565852": tokens.ink_soft,
        "#5D5E59": tokens.ink_soft,
        "#6E6D67": tokens.muted,
        "#76756F": tokens.muted,
        "#77766F": tokens.muted,
        "#7A7973": tokens.muted,
        "#B8B8B1": tokens.disabled,
        "#BFC3BD": tokens.border_strong,
        "#DEDCD5": tokens.border,
        "#DCDAD3": tokens.border,
        "#E1DFD9": tokens.border,
        "#E2DFD8": tokens.border,
        "#E4E1DA": tokens.border,
        "#E8E6E0": tokens.border,
        "#E9E6E0": tokens.border,
        "#2878E8": tokens.accent,
        "#1E6EDC": tokens.accent_hover,
        "#165FC5": tokens.accent_pressed,
        "#216FD6": tokens.accent_hover,
        "#80B4F3": tokens.accent_border,
        "#80B3F3": tokens.accent_border,
        "#86B4F0": tokens.accent_border,
        "#8CB8F4": tokens.accent_border,
        "#72AAF1": tokens.accent_border,
        "#C8DAF3": tokens.accent_border,
        "#EDF5FF": tokens.accent_soft,
        "#F2F7FF": tokens.accent_soft,
        "#D7E8FF": tokens.accent_border,
        "#E5F0FF": tokens.accent_soft_hover,
        "#F4F8FF": tokens.accent_soft,
        "#F3F8FF": tokens.accent_soft,
    }
    pattern = re.compile("|".join(re.escape(color) for color in replacements), re.IGNORECASE)
    return pattern.sub(lambda match: replacements[match.group(0).upper()], source)


def build_stylesheet(tokens: AppearanceTokens) -> str:
    sheet = _replace_tokens(MAIN_WINDOW_QSS + DIALOG_QSS, tokens)
    vertical_padding = {"spacious": 10, "balanced": 8, "compact": 6}[tokens.density]
    input_height = max(24, tokens.control_height - 14)
    sheet += f"""
QPushButton {{ padding-top: {vertical_padding}px; padding-bottom: {vertical_padding}px; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QKeySequenceEdit, QComboBox {{ min-height: {input_height}px; }}
"""
    return sheet


def apply_style(app, appearance: dict[str, Any] | None = None) -> AppearanceTokens:
    tokens = set_current_appearance(appearance)
    configure_motion(tokens.motion_profile, tokens.reduce_motion)
    app.setStyleSheet(build_stylesheet(tokens))
    return tokens
