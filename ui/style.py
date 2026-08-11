"""ScreenTranslator 的统一暖白极简视觉系统。"""

from __future__ import annotations



MAIN_WINDOW_QSS = """
QWidget#Root { background: #F7F5F1; }
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
QDialog { background: #F7F5F1; }
QDialog QLabel#SettingsTitle { color: #292D2A; font-size: 24px; font-weight: 700; }
QDialog QLabel#SettingsSubtitle { color: #77766F; font-size: 13px; }
QFrame#SettingsShell { background: #FFFEFC; border: 1px solid #E2DFD8; border-radius: 16px; }
QFrame#SettingsDivider { background: #E9E6E0; max-width: 1px; border: none; }
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


def apply_style(app) -> None:
    app.setStyleSheet(MAIN_WINDOW_QSS + DIALOG_QSS)
