"""Runtime appearance tokens for the application chrome.

The capture overlay follows the selected accent while keeping its neutral
mask and paper surfaces.  These tokens cover the application shell,
settings, capture feedback, tray icon and lightweight status surfaces
without leaking into the user-configurable translation overlay colours.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication


PALETTE_PRESETS = (
    ("warm_paper", "暖纸", "温和、自然的纸张底色"),
    ("mist", "雾蓝", "更清透的冷静灰蓝"),
    ("midnight", "墨夜", "低眩光的深色阅读环境"),
    ("system", "跟随系统", "随系统深浅色自动切换"),
)

ACCENT_PRESETS = (
    ("#2878E8", "曜蓝"),
    ("#168A82", "松石"),
    ("#B66A14", "琥珀"),
    ("#7258D6", "藤紫"),
)

MOTION_PRESETS = (
    ("flow", "灵动", "短促回弹，反馈更鲜明"),
    ("calm", "舒缓", "节奏从容，过渡更柔和"),
    ("minimal", "精简", "只保留必要状态变化"),
)

DENSITY_PRESETS = (
    ("spacious", "舒展", "更多呼吸感"),
    ("balanced", "均衡", "默认布局"),
    ("compact", "紧凑", "提高信息密度"),
)

SURFACE_PRESETS = (
    ("clean", "纯净", "减少层级与边界"),
    ("layered", "柔和层次", "用细边框区分区域"),
)


@dataclass(frozen=True, slots=True)
class AppearanceTokens:
    palette: str
    accent: str
    root: str
    surface: str
    surface_alt: str
    surface_hover: str
    ink: str
    ink_soft: str
    muted: str
    border: str
    border_strong: str
    disabled: str
    accent_hover: str
    accent_pressed: str
    accent_soft: str
    accent_soft_hover: str
    accent_border: str
    on_accent: str
    dark: bool
    density: str
    surface_style: str
    motion_profile: str
    reduce_motion: bool
    control_height: int
    nav_height: int
    page_spacing: int
    card_padding: int
    main_spacing: int


_PALETTES: dict[str, dict[str, str]] = {
    "warm_paper": {
        "root": "#F7F5F1",
        "surface": "#FFFEFC",
        "surface_alt": "#FBFAF7",
        "surface_hover": "#F8F9F8",
        "ink": "#292D2A",
        "ink_soft": "#4F514D",
        "muted": "#77766F",
        "border": "#E2DFD8",
        "border_strong": "#C8C6BF",
        "disabled": "#B8B8B1",
    },
    "mist": {
        "root": "#EEF3F5",
        "surface": "#FBFDFE",
        "surface_alt": "#F4F8FA",
        "surface_hover": "#F6FAFC",
        "ink": "#263137",
        "ink_soft": "#4C5C64",
        "muted": "#6C7A80",
        "border": "#D8E1E5",
        "border_strong": "#BECED5",
        "disabled": "#AEB9BE",
    },
    "midnight": {
        "root": "#1E2222",
        "surface": "#282D2D",
        "surface_alt": "#232828",
        "surface_hover": "#2D3433",
        "ink": "#F2F0EA",
        "ink_soft": "#D0D2CD",
        "muted": "#A3ADA9",
        "border": "#3B4341",
        "border_strong": "#596460",
        "disabled": "#747E7A",
    },
}

_DENSITY = {
    "spacious": (46, 46, 14, 19, 24),
    "balanced": (42, 42, 12, 18, 22),
    "compact": (36, 36, 9, 15, 16),
}


def _system_palette() -> str:
    try:
        hints = QGuiApplication.styleHints()
        if hints is not None and hints.colorScheme() == Qt.ColorScheme.Dark:
            return "midnight"
    except (AttributeError, RuntimeError):
        pass
    return "warm_paper"


def _mix(start: str, end: str, amount: float) -> str:
    amount = max(0.0, min(1.0, amount))
    a = QColor(start)
    b = QColor(end)
    color = QColor.fromRgbF(
        a.redF() + (b.redF() - a.redF()) * amount,
        a.greenF() + (b.greenF() - a.greenF()) * amount,
        a.blueF() + (b.blueF() - a.blueF()) * amount,
    )
    return color.name(QColor.NameFormat.HexRgb).upper()


def _contrast_text(background: str) -> str:
    color = QColor(background)
    luminance = (
        0.2126 * color.redF() + 0.7152 * color.greenF() + 0.0722 * color.blueF()
    )
    return "#202421" if luminance > 0.62 else "#FFFFFF"


def _valid_accent(value: Any) -> str:
    text = str(value or "#2878E8").strip()
    color = QColor(text)
    if not color.isValid() or color.alpha() != 255:
        return "#2878E8"
    return color.name(QColor.NameFormat.HexRgb).upper()


def resolve_tokens(appearance: dict[str, Any] | None = None) -> AppearanceTokens:
    appearance = appearance or {}
    requested_palette = str(appearance.get("palette", "warm_paper"))
    palette_name = (
        requested_palette
        if requested_palette == "system" or requested_palette in _PALETTES
        else "warm_paper"
    )
    resolved_palette = _system_palette() if palette_name == "system" else palette_name
    palette = _PALETTES[resolved_palette]
    dark = resolved_palette == "midnight"

    accent = _valid_accent(appearance.get("accent", "#2878E8"))
    surface_style = str(appearance.get("surface", "layered"))
    if surface_style not in {item[0] for item in SURFACE_PRESETS}:
        surface_style = "layered"
    density = str(appearance.get("density", "balanced"))
    if density not in _DENSITY:
        density = "balanced"
    motion_profile = str(appearance.get("motion_profile", "flow"))
    if motion_profile not in {item[0] for item in MOTION_PRESETS}:
        motion_profile = "flow"
    reduce_motion = bool(appearance.get("reduce_motion", False))

    root = palette["root"]
    surface = palette["surface"]
    surface_alt = surface if surface_style == "clean" else palette["surface_alt"]
    surface_hover = _mix(surface, accent, 0.035 if not dark else 0.075)
    accent_hover = _mix(accent, "#FFFFFF" if dark else "#000000", 0.11)
    accent_pressed = _mix(accent, "#FFFFFF" if dark else "#000000", 0.2)
    accent_soft = _mix(surface, accent, 0.11 if not dark else 0.18)
    accent_soft_hover = _mix(surface, accent, 0.17 if not dark else 0.25)
    accent_border = _mix(palette["border"], accent, 0.58)
    control_height, nav_height, page_spacing, card_padding, main_spacing = _DENSITY[density]

    return AppearanceTokens(
        palette=palette_name,
        accent=accent,
        root=root,
        surface=surface,
        surface_alt=surface_alt,
        surface_hover=surface_hover,
        ink=palette["ink"],
        ink_soft=palette["ink_soft"],
        muted=palette["muted"],
        border=palette["border"],
        border_strong=palette["border_strong"],
        disabled=palette["disabled"],
        accent_hover=accent_hover,
        accent_pressed=accent_pressed,
        accent_soft=accent_soft,
        accent_soft_hover=accent_soft_hover,
        accent_border=accent_border,
        on_accent=_contrast_text(accent),
        dark=dark,
        density=density,
        surface_style=surface_style,
        motion_profile=motion_profile,
        reduce_motion=reduce_motion,
        control_height=control_height,
        nav_height=nav_height,
        page_spacing=page_spacing,
        card_padding=card_padding,
        main_spacing=main_spacing,
    )


_current_appearance: dict[str, Any] = {}
_current_tokens = resolve_tokens()


def set_current_appearance(appearance: dict[str, Any] | None) -> AppearanceTokens:
    global _current_appearance, _current_tokens
    _current_appearance = dict(appearance or {})
    _current_tokens = resolve_tokens(_current_appearance)
    return _current_tokens


def current_appearance() -> dict[str, Any]:
    return dict(_current_appearance)


def current_tokens() -> AppearanceTokens:
    return _current_tokens
