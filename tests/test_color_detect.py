"""覆盖层文字/背景颜色自动识别测试（图像 BGR 存储，hex 为真实 RGB）。"""

import numpy as np

from utils.image_utils import (
    color_distance,
    contrast_ratio,
    detect_colors,
    dominant_color,
    ensure_text_contrast,
    purify_text_color,
    rgb_to_hex,
    sanitize_background,
)


def bgr_from_hex(hex_color: str) -> tuple[int, int, int]:
    """'#RRGGBB' -> BGR 三元组。"""
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    return (b, g, r)


def _region(background_hex: str, text_hex: str, text_ratio: float = 0.3):
    bg = bgr_from_hex(background_hex)
    fg = bgr_from_hex(text_hex)
    image = np.full((40, 80, 3), bg, dtype=np.uint8)
    th = int(40 * text_ratio)
    tw = int(80 * text_ratio)
    image[10 : 10 + th, 10 : 10 + tw] = fg
    return image


def test_detect_light_background_dark_text():
    image = _region("#FFFFFF", "#000000")
    bg, text, luminance = detect_colors(image, 0, 0, 80, 40)
    assert bg == "#FFFFFF"
    assert text == "#000000"
    assert luminance >= 0.55


def test_detect_dark_background_light_text():
    image = _region("#1E1E1E", "#FFFFFF")
    bg, text, luminance = detect_colors(image, 0, 0, 80, 40)
    assert bg == "#1E1E1E"
    assert text == "#FFFFFF"
    assert luminance < 0.55


def test_detect_region_offset_and_bounds():
    canvas = np.full((100, 100, 3), bgr_from_hex("#FFFFFF"), dtype=np.uint8)
    canvas[45:55, 45:55] = bgr_from_hex("#000000")
    bg, text, _ = detect_colors(canvas, 30, 30, 40, 40)
    assert bg == "#FFFFFF"
    assert text == "#000000"


def test_detect_solid_region_falls_back():
    image = np.full((40, 40, 3), bgr_from_hex("#C8C8C8"), dtype=np.uint8)
    bg, text, luminance = detect_colors(image, 0, 0, 40, 40)
    assert bg == "#FFFFFF"
    assert text == "#000000"
    assert luminance >= 0.55


def test_detect_colorful_background_white_text():
    image = _region("#7C3AED", "#FFFFFF")
    bg, text, _ = detect_colors(image, 0, 0, 80, 40)
    assert bg == "#7C3AED"
    assert text == "#FFFFFF"


def test_detect_noisy_background_dark_text():
    rng = np.random.default_rng(7)
    image = np.full((40, 80, 3), bgr_from_hex("#F5F5F5"), dtype=np.uint8)
    image = np.clip(image + rng.normal(0, 6, image.shape), 0, 255).astype(np.uint8)
    image[12:28, 12:40] = (20, 20, 20)
    bg, text, luminance = detect_colors(image, 0, 0, 80, 40)
    assert text == "#000000"
    assert luminance >= 0.55
    bg_rgb = (int(bg[1:3], 16), int(bg[3:5], 16), int(bg[5:7], 16))
    assert min(bg_rgb) >= 230


def test_detect_text_dominates_area():
    """文字占近半时仍应识别出背景（多行文字行块常见场景）。"""
    image = _region("#1E1E1E", "#FFFFFF", text_ratio=0.45)
    bg, text, _ = detect_colors(image, 0, 0, 80, 40)
    assert bg == "#1E1E1E"
    assert text == "#FFFFFF"


def test_detect_gradient_background():
    gradient = np.linspace(200, 255, 80, dtype=np.uint8)
    image = np.repeat(gradient[np.newaxis, :, np.newaxis], 40, axis=0)
    image = np.repeat(image, 3, axis=2)
    image[12:28, 12:40] = (20, 20, 20)
    bg, text, luminance = detect_colors(image, 0, 0, 80, 40)
    assert text == "#000000"
    assert luminance >= 0.55


def test_detect_tight_text_block():
    """OCR 框贴紧文字（文字顶到左上角）时，右侧空白仍能识别出背景。"""
    bg = bgr_from_hex("#24303D")
    fg = bgr_from_hex("#C6D4DF")
    image = np.full((40, 80, 3), bg, dtype=np.uint8)
    image[4:30, 0:52] = fg
    detected_bg, detected_text, luminance = detect_colors(image, 0, 0, 80, 40)
    assert detected_bg == "#24303D"
    assert detected_text == "#FFFFFF"
    assert luminance < 0.55


def test_detect_page_with_yellow_icon():
    """背景含黄色图标等离群元素时，鲁棒剔除后仍识别正确背景。"""
    bg = bgr_from_hex("#24303D")
    fg = bgr_from_hex("#C6D4DF")
    image = np.full((40, 80, 3), bg, dtype=np.uint8)
    image[6:26, 4:44] = fg
    image[2:10, 70:80] = bgr_from_hex("#F0C83C")
    detected_bg, detected_text, _ = detect_colors(image, 0, 0, 80, 40)
    assert detected_bg == "#24303D"
    assert detected_text == "#FFFFFF"


def test_dominant_color_light_page():
    image = np.full((200, 300, 3), bgr_from_hex("#FFFFFF"), dtype=np.uint8)
    image[60:140, 60:180] = bgr_from_hex("#141414")
    result = dominant_color(image)
    assert result is not None
    assert rgb_to_hex(result) == "#FFFFFF"


def test_dominant_color_steam_page():
    image = np.full((200, 300, 3), bgr_from_hex("#24303D"), dtype=np.uint8)
    image[30:170, 40:200] = bgr_from_hex("#C6D4DF")
    result = dominant_color(image)
    assert result is not None
    assert rgb_to_hex(result) == "#24303D"


def test_dominant_color_returns_none_when_no_majority():
    rng = np.random.default_rng(3)
    image = rng.integers(0, 255, size=(100, 100, 3), dtype=np.uint8)
    assert dominant_color(image) is None


def test_sanitize_background_corrects_wrong_block():
    global_bg = bgr_from_hex("#24303D")
    wrong = "#1B130D"  # 暖棕（误识别）
    assert color_distance(wrong, "#24303D") > 55
    assert sanitize_background(wrong, global_bg) == "#24303D"


def test_sanitize_background_keeps_plausible():
    global_bg = bgr_from_hex("#24303D")
    close = "#22303A"
    assert sanitize_background(close, global_bg) == close


def test_sanitize_background_keeps_red_banner():
    """红色横幅（高饱和独立色块）不能被页面基调吞成深蓝灰。"""
    global_bg = bgr_from_hex("#24303D")
    red = "#B05C5C"
    assert color_distance(red, "#24303D") > 55
    assert sanitize_background(red, global_bg) == red


def test_sanitize_background_keeps_light_card():
    """白色/浅色卡片（亮度差异大的独立色块）不能被页面基调吞掉。"""
    global_bg = bgr_from_hex("#24303D")
    white = "#FFFFFF"
    assert sanitize_background(white, global_bg) == white


def test_sanitize_background_still_corrects_noisy_low_saturation():
    """低饱和且亮度接近基调的采样噪声仍然回退到页面基调。"""
    global_bg = bgr_from_hex("#24303D")
    noisy = "#1B130D"  # 暖棕噪声：低饱和、亮度接近深蓝灰
    assert sanitize_background(noisy, global_bg) == "#24303D"


def test_sanitize_background_keeps_neutral_gray_text_background():
    """中性灰文本行背景不能被回退成页面基调色（灰色识别成黑色的根因）。"""
    global_bg = bgr_from_hex("#2B2B2E")
    gray = "#403F42"
    assert color_distance(gray, "#2B2B2E") > 55
    assert sanitize_background(gray, global_bg) == gray


def test_sanitize_background_keeps_light_gray_card():
    global_bg = bgr_from_hex("#1B2837")
    light_gray = "#C6C6C6"
    assert sanitize_background(light_gray, global_bg) == light_gray


def test_ensure_text_contrast_keeps_good_contrast():
    assert ensure_text_contrast("#FFFFFF", "#1B2837") == "#FFFFFF"
    assert ensure_text_contrast("#000000", "#FFFFFF") == "#000000"


def test_ensure_text_contrast_fixes_low_contrast():
    # 深灰文字在深蓝灰背景上几乎不可读 -> 改为白色
    assert ensure_text_contrast("#443D38", "#1B2837") == "#FFFFFF"
    # 极浅灰文字在白色背景上 -> 改为黑色
    assert ensure_text_contrast("#EEEEEE", "#FFFFFF") == "#000000"


def test_contrast_ratio_wcag():
    assert contrast_ratio("#000000", "#FFFFFF") == 21.0
    assert contrast_ratio("#FFFFFF", "#1B2837") >= 10.0
    # 黑字配 Steam 深蓝黑背景：肉眼不可读，对比度必须远低于 4.5
    assert contrast_ratio("#000000", "#1B2837") < 3.0


def test_ensure_text_contrast_fixes_black_on_dark():
    """黑字配深底蓝黑（曼哈顿距离 122 但 WCAG 不可读）必须被换成白色。"""
    assert ensure_text_contrast("#000000", "#1B2837") == "#FFFFFF"


def test_purify_text_color_removes_hue_shift():
    """Steam 灰白字采样出紫灰/绿灰等低饱和脏色 -> 归一为纯白（深底）或纯黑（浅底）。"""
    assert purify_text_color("#AC9EB8", "#1B2837") == "#FFFFFF"
    assert purify_text_color("#ACB2A4", "#1B2837") == "#FFFFFF"
    assert purify_text_color("#696F7E", "#1B2837") == "#FFFFFF"
    assert purify_text_color("#ACB2B8", "#F5F5F5") == "#000000"


def test_purify_text_color_keeps_saturated():
    """明显彩色文字（蓝色链接、黄色标题）保留原色。"""
    assert purify_text_color("#66C0F4", "#1B2837") == "#66C0F4"
    assert purify_text_color("#FFD700", "#1B2837") == "#FFD700"


def test_detect_purifies_gray_text_on_dark():
    """深底浅灰文字即使被采样出色相漂移，最终也归一为纯白。"""
    image = np.full((40, 80, 3), bgr_from_hex("#1B2837"), dtype=np.uint8)
    image[10:30, 10:60] = bgr_from_hex("#AC9EB8")
    bg, text, luminance = detect_colors(image, 0, 0, 80, 40)
    assert bg == "#1B2837"
    assert text == "#FFFFFF"
