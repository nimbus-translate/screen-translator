"""覆盖层字体回退测试：自定义拉丁字体必须能正确回退渲染中文。"""

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def _build_window(app, custom_family: str):
    from app.config import AppConfig
    from ui.translation_overlay import TranslationOverlayWindow

    config = AppConfig()
    config.set("overlay.font_family", custom_family)
    return TranslationOverlayWindow(config)


def _render_text(font) -> QImage:
    image = QImage(360, 90, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor("#FFFFFF"))
    painter.setFont(font)
    painter.drawText(
        QRect(10, 10, 340, 70),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        "你好世界，游戏结束",
    )
    painter.end()
    return image


def test_font_families_chain(app):
    window = _build_window(app, "Agency FB")
    families = window._font_families()
    assert families[0] == "Agency FB"
    assert any("YaHei" in family for family in families)
    assert "SimSun" in families


def test_fallback_chain_renders_like_default(app):
    """自定义字体+回退链 的渲染必须与默认字体完全一致（diff=0）。"""
    window = _build_window(app, "Agency FB")
    fallback = window._make_font(window._font_families(), 24)
    window_default = _build_window(app, "")
    default_font = window_default._make_font(window_default._font_families(), 24)
    assert _render_text(fallback) == _render_text(default_font)


def _fit(window, text: str, width: int, height: int):
    from PySide6.QtCore import QRectF

    return window._fit_font(text, QRectF(0, 0, width, height), 4)


def test_fit_font_keeps_preferred_size_for_roomy_rect(app):
    window = _build_window(app, "")
    font = _fit(window, "你好世界", 300, 60)
    assert font.pointSize() == int(window._config.get("overlay.font_size", 18))


def test_fit_font_shrinks_for_small_rect(app):
    window = _build_window(app, "")
    font = _fit(window, "包围所有敌人并且开始攻击", 120, 26)
    assert font.pointSize() < int(window._config.get("overlay.font_size", 18))


def test_fit_font_allows_below_configured_min_for_tiny_rect(app):
    """极小按钮：配置下限 8pt 仍放不下时允许压到更低（硬下限 5pt）。"""
    window = _build_window(app, "")
    window._config.set("overlay.min_font_size", 8)
    font = _fit(window, "包围所有敌人并且开始攻击", 60, 16)
    assert font.pointSize() <= 8
    assert font.pointSize() >= 3


def test_fit_font_wraps_long_text_in_narrow_rect(app):
    window = _build_window(app, "")
    font = _fit(window, "这是一段比较长的翻译文本需要自动换行显示", 90, 40)
    from PySide6.QtGui import QFontMetrics

    metrics = QFontMetrics(font)
    bounds = metrics.boundingRect(
        0, 0, 90 - 8, 100000, __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.TextFlag.TextWordWrap,
        "这是一段比较长的翻译文本需要自动换行显示",
    )
    # 换行后高度受新预算约束（块高 40 - 2 = 38，至少单行）
    assert bounds.height() <= max(38, metrics.height())


def test_fit_font_scales_with_block_height(app):
    """字号随块高自适应：大按钮配大字、小按钮配小字（自动调整文字大小）。"""
    window = _build_window(app, "")
    big = _fit(window, "包含", 120, 60)
    small = _fit(window, "包含", 40, 16)
    assert big.pointSize() > small.pointSize()
    # 大按钮字号应明显大于配置下限（8pt），避免“大框小字”
    assert big.pointSize() >= 12


def test_size_groups_unify_same_level_font(app):
    """同屏高度相近的块归组统一字号，消除 OCR 框高噪声导致的同级参差。"""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor
    from ui.translation_overlay import Block

    window = _build_window(app, "")
    blocks = [
        Block(QRectF(0, 0, 80, h), "文字", QColor("#FFFFFF"), "#000000")
        for h in (16, 17, 18, 30, 31)
    ]
    window.set_blocks(blocks)
    fonts = {
        i: window._fit_font("文字", b.rect, 4, size_height=window._size_groups.get(i)).pointSize()
        for i, b in enumerate(blocks)
    }
    # 16/17/18 同组统一；30/31 同组统一且与上一组不同
    assert fonts[0] == fonts[1] == fonts[2]
    assert fonts[3] == fonts[4]
    assert fonts[0] != fonts[3]
