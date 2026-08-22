"""图像工具：格式转换、亮度估算、缩放。"""

from __future__ import annotations

import numpy as np


def mss_bgra_to_bgr(raw: bytes, width: int, height: int) -> np.ndarray:
    """mss 返回 BGRA 字节串 -> OpenCV BGR 数组。"""
    arr = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 4)
    return arr[:, :, :3].copy()


def luminance_of(image_bgr: np.ndarray, x: int, y: int, w: int, h: int) -> float:
    """估算一块区域的平均亮度，0（黑）~1（白）。"""
    img_h, img_w = image_bgr.shape[:2]
    x0 = max(0, min(x, img_w - 1))
    y0 = max(0, min(y, img_h - 1))
    x1 = max(x0 + 1, min(x + w, img_w))
    y1 = max(y0 + 1, min(y + h, img_h))
    block = image_bgr[y0:y1, x0:x1]
    if block.size == 0:
        return 0.5
    gray = block.mean(axis=2) if block.ndim == 3 else block
    return float(gray.mean() / 255.0)


def text_color_for_luminance(luminance: float) -> str:
    """背景亮则用深色文字，背景暗则用浅色文字。"""
    return "#000000" if luminance >= 0.55 else "#FFFFFF"


def rgb_to_hex(rgb) -> str:
    """把 BGR 数组/元组转成 '#RRGGBB'（图像以 BGR 存储，通道需反转）。"""
    b, g, r = (int(round(float(v))) for v in rgb[:3])
    return "#{:02X}{:02X}{:02X}".format(max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def color_distance(a: str, b: str) -> int:
    """两个 hex 颜色的 RGB 曼哈顿距离。"""
    ar, ag, ab = hex_to_rgb(a)
    br, bg_, bb = hex_to_rgb(b)
    return abs(ar - br) + abs(ag - bg_) + abs(ab - bb)


def dominant_color(image_bgr: np.ndarray) -> np.ndarray | None:
    """页面基调色：整图 4bit 量化主峰。

    4bit（16 级/通道）量化让纯色页面背景在颜色桶上高度集中，抗渐变与
    噪点分散；覆盖层/横幅等次要色占比低时不会抢占主峰。
    """
    height, width = image_bgr.shape[:2]
    if height < 4 or width < 4:
        return None
    step = max(1, max(height, width) // 240)
    sub = image_bgr[::step, ::step]
    keys = (
        (sub[:, :, 2].astype(np.uint16) >> 4) << 8
        | (sub[:, :, 1].astype(np.uint16) >> 4) << 4
        | (sub[:, :, 0].astype(np.uint16) >> 4)
    ).reshape(-1)
    counts = np.bincount(keys, minlength=4096)
    peak = int(np.argmax(counts))
    if int(counts[peak]) < int(keys.size) * 0.1:
        return None
    mask = keys == peak
    if int(mask.sum()) < 8:
        return None
    return np.median(sub.reshape(-1, 3)[mask], axis=0)


def sanitize_background(
    bg_hex: str,
    global_bg: np.ndarray | None,
    threshold: int = 55,
    saturation_threshold: int = 40,
    luminance_gap: float = 0.35,
) -> str:
    """块级识别背景色与页面基调交叉验证，只纠正“采样噪声”，不吞独立色块。

    历史 bug：红色横幅/白色卡片这类与页面基调（如 Steam 深蓝灰）色差大的
    独立色块，会被一刀切“纠正”成页面基调色。修正规则：
    - 块级背景饱和度较高（红色横幅、黄色按钮等明显彩色）→ 保留；
    - 块级背景与页面基调亮度差异大（白色卡片 vs 深色页面）→ 保留；
    - 只有低饱和且亮度接近基调的采样噪声才回退到页面基调色。
    """
    if global_bg is None:
        return bg_hex
    r, g, b = hex_to_rgb(bg_hex)
    saturation = max(r, g, b) - min(r, g, b)
    bg_lum = (r + g + b) / (3.0 * 255.0)
    reference = rgb_to_hex(global_bg)
    # 中性灰（三通道差 ≤8）且非极端黑/白：真实文本行背景（深色页面上的灰条），
    # 保留原色——历史 bug：中灰 #403F42 被当成“低饱和噪声”回退成页面基调
    # #2B2B2E（用户：灰色识别成黑色）。暖棕/偏色噪声（如 #1B130D）仍走纠偏。
    if saturation <= 8 and 0.08 <= bg_lum <= 0.92:
        return bg_hex
    if saturation >= saturation_threshold or abs(bg_lum - hex_luminance(reference)) >= luminance_gap:
        return bg_hex
    if color_distance(bg_hex, reference) > threshold:
        return reference
    return bg_hex


def hex_luminance(value: str) -> float:
    r, g, b = hex_to_rgb(value)
    return (r + g + b) / (3.0 * 255.0)


def relative_luminance(value: str) -> float:
    """WCAG 相对亮度（sRGB 线性化），0（黑）~1（白）。"""
    r, g, b = (c / 255.0 for c in hex_to_rgb(value))

    def linearize(channel: float) -> float:
        return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4

    return 0.2126 * linearize(r) + 0.7152 * linearize(g) + 0.0722 * linearize(b)


def contrast_ratio(text_hex: str, bg_hex: str) -> float:
    """WCAG 对比度：黑 vs 白 = 21，黑 vs #1B2837 ≈ 1.5（不可读）。"""
    l1 = relative_luminance(text_hex)
    l2 = relative_luminance(bg_hex)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def purify_text_color(text_hex: str, bg_hex: str, saturation_threshold: int = 45) -> str:
    """低饱和文字归一到黑白，消除采样色相漂移（灰白字变成紫灰/绿灰）。

    只有明显带彩色的文字（蓝色链接、黄色标题、红色警示等）才保留原色。
    """
    r, g, b = hex_to_rgb(text_hex)
    saturation = max(r, g, b) - min(r, g, b)
    if saturation >= saturation_threshold:
        return text_hex
    return text_color_for_luminance(hex_luminance(bg_hex))


def ensure_text_contrast(text_hex: str, bg_hex: str, min_ratio: float = 4.5) -> str:
    """背景被纠正/替换后，若文字与背景 WCAG 对比不足则按背景亮度选黑白，保证可读。"""
    if contrast_ratio(text_hex, bg_hex) >= min_ratio:
        return text_hex
    return text_color_for_luminance(hex_luminance(bg_hex))


def detect_colors(image_bgr: np.ndarray, x: int, y: int, w: int, h: int) -> tuple[str, str, float]:
    """识别一个文本区域的背景色与文字色。

    方法：
    1. 背景色取区域边缘带像素的中位数（抗噪、抗 OCR 框不贴紧）；
    2. 文字色取区域内与背景色差最大的像素簇（色差 Otsu 二值化）的中位数；
    3. 对比度过低或近似纯色时退化为亮度法。
    返回 (背景色 hex, 文字色 hex, 背景亮度 0~1)。
    """
    img_h, img_w = image_bgr.shape[:2]
    x0 = max(0, min(x, img_w - 1))
    y0 = max(0, min(y, img_h - 1))
    x1 = max(x0 + 1, min(x + w, img_w))
    y1 = max(y0 + 1, min(y + h, img_h))
    region = image_bgr[y0:y1, x0:x1]
    if region.size == 0:
        return "#000000", "#FFFFFF", 0.5

    background = _sample_background(region)
    if background is None:
        return _fallback_colors(region)
    text = _sample_text(region, background)
    if text is None:
        return _fallback_colors(region)

    bg_lum = float(np.asarray(background, dtype=np.float32).mean() / 255.0)
    text_lum = float(np.asarray(text, dtype=np.float32).mean() / 255.0)
    # 文字与背景对比度不足时视为识别失败，退化
    if abs(bg_lum - text_lum) * 255.0 < 25:
        return _fallback_colors(region)
    bg_hex = rgb_to_hex(background)
    text_hex = purify_text_color(rgb_to_hex(text), bg_hex)
    return bg_hex, text_hex, bg_lum


def _sample_background(region: np.ndarray):
    """背景色：取文字笔画周围的环带像素中位数（抗描边/阴影污染）。

    历史问题：OCR 框贴紧文字，四角小块会直接采到笔画；而带描边/阴影的
    文字（如红底白字黑描边）会让“四角+边缘”候选池被黑色描边带偏，
    背景被识别成黑色/深色。改进：先用“与整体中位数色差 Otsu”粗分文字，
    再取文字 mask 膨胀后减去文字本身得到的环带，环带几乎都是真实背景。
    """
    height, width = region.shape[:2]
    if height < 6 or width < 6:
        return None

    flat = region.reshape(-1, 3)
    center = np.median(flat, axis=0)
    diff = np.abs(region.astype(np.int16) - center.astype(np.int16)).sum(axis=2)
    norm = np.clip(diff / 3.0, 0, 255).astype(np.uint8)
    text_mask = norm > _otsu_threshold_np(norm)
    text_count = int(text_mask.sum())
    # 无明显文字（近似纯色）或文字占满全框：直接取整体中位数
    if text_count < 8 or text_count > region.size * 0.9:
        return center

    dilated = _binary_dilate(text_mask, radius=4)
    ring = dilated & ~text_mask
    ring_pixels = region[ring]
    if ring_pixels.size < 8:
        return None
    # 环带聚类取最大颜色簇：背景簇占环带多数，抗文字描边/阴影/抗锯齿边缘
    # 把中位数拉暗（EU3 类灰色文本行被识别成黑色的根因）
    quant = (ring_pixels.astype(np.uint16) >> 4) << 4
    keys = (
        (quant[:, 2] << 8) | (quant[:, 1] << 4) | quant[:, 0]
    )
    counts = np.bincount(keys, minlength=4096)
    top_key = int(np.argmax(counts))
    top_ratio = int(counts[top_key]) / ring_pixels.shape[0]
    if top_ratio >= 0.25:
        mask = keys == top_key
        return np.median(ring_pixels[mask], axis=0)
    return np.median(ring_pixels, axis=0)


def _sample_text(region: np.ndarray, background: np.ndarray):
    """文字色：区域内与背景色差最大的像素簇的中位数。"""
    height, width = region.shape[:2]
    if height * width < 16:
        return None
    # 曼哈顿色差归一化到 0-255
    diff = np.abs(region.astype(np.int16) - background.astype(np.int16)).sum(axis=2)
    norm = np.clip(diff / 3.0, 0, 255).astype(np.uint8)
    threshold_value = _otsu_threshold_np(norm)
    mask = norm > threshold_value
    # 高差像素太少（近似纯色）→ 识别失败
    if int(mask.sum()) < max(8, height * width * 0.02):
        return None
    text_pixels = region[mask]
    return np.median(text_pixels, axis=0)


def _otsu_threshold_np(values: np.ndarray) -> int:
    """numpy 版 Otsu 阈值（cv2 在单峰/极端分布下会返回 0，这里更稳）。"""
    hist = np.bincount(values.reshape(-1), minlength=256)
    total = int(values.size)
    if total == 0:
        return 0
    sum_all = float(np.dot(np.arange(256, dtype=np.float64), hist))
    sum_back = 0.0
    weight_back = 0
    best_threshold = 0
    max_variance = -1.0
    for threshold in range(256):
        weight_back += int(hist[threshold])
        if weight_back == 0:
            continue
        weight_front = total - weight_back
        if weight_front == 0:
            break
        sum_back += threshold * float(hist[threshold])
        mean_back = sum_back / weight_back
        mean_front = (sum_all - sum_back) / weight_front
        variance = weight_back * weight_front * (mean_back - mean_front) ** 2
        if variance > max_variance:
            max_variance = variance
            best_threshold = threshold
    return best_threshold


def _binary_dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    """Dilate a small boolean text mask without pulling cv2 into the core app."""
    source = np.asarray(mask, dtype=bool)
    if radius <= 0 or source.size == 0:
        return source.copy()
    height, width = source.shape
    padded = np.pad(source, radius, mode="constant", constant_values=False)
    result = np.zeros_like(source)
    diameter = radius * 2 + 1
    for offset_y in range(diameter):
        for offset_x in range(diameter):
            result |= padded[offset_y : offset_y + height, offset_x : offset_x + width]
    return result


def _resize_bgr(
    image_bgr: np.ndarray, width: int, height: int, *, upscale: bool
) -> np.ndarray:
    """Resize BGR pixels with Pillow for the lightweight Windows build."""
    from PIL import Image

    rgb = np.ascontiguousarray(image_bgr[:, :, ::-1])
    source = Image.fromarray(rgb, mode="RGB")
    resample = Image.Resampling.BICUBIC if upscale else Image.Resampling.LANCZOS
    resized = source.resize((max(1, int(width)), max(1, int(height))), resample=resample)
    return np.asarray(resized, dtype=np.uint8)[:, :, ::-1].copy()


def _fallback_colors(region: np.ndarray) -> tuple[str, str, float]:
    """退化：按整体亮度选择黑白，保证可读。"""
    gray = region.mean(axis=2) if region.ndim == 3 else region
    luminance = float(gray.mean() / 255.0)
    if luminance >= 0.55:
        return "#FFFFFF", "#000000", luminance
    return "#000000", "#FFFFFF", luminance


def resize_to_max(image_bgr: np.ndarray, max_side: int = 4096) -> tuple[np.ndarray, float]:
    """超长边缩放到 max_side，返回 (新图, 缩放比例)。"""
    h, w = image_bgr.shape[:2]
    long_side = max(h, w)
    if long_side <= max_side:
        return image_bgr, 1.0
    scale = max_side / long_side
    resized = _resize_bgr(image_bgr, round(w * scale), round(h * scale), upscale=False)
    return resized, scale


def resize_for_ocr(
    image_bgr: np.ndarray,
    min_side: int = 1600,
    max_side: int = 4096,
    max_upscale: float = 3.0,
) -> tuple[np.ndarray, float]:
    """OCR 前自适应缩放：小图放大、超大图缩小，返回 (新图, 缩放比例)。

    小图（如 Steam 弹窗里的游戏截图，长边 400~800）里的文字只有 8~12px，
    PaddleOCR 对这类小字检测/识别率很低，导致按钮文字整片漏识别、覆盖层
    “涂成一块”。先放大到长边 min_side 再识别，行数可翻 3~4 倍；坐标随后
    按 scale 还原到原图坐标。
    """
    h, w = image_bgr.shape[:2]
    long_side = max(h, w)
    if long_side <= min_side:
        scale = min(min_side / long_side, max_upscale)
        if scale <= 1.0:
            return image_bgr, 1.0
        resized = _resize_bgr(
            image_bgr, round(w * scale), round(h * scale), upscale=True
        )
        return resized, scale
    if long_side > max_side:
        scale = max_side / long_side
        resized = _resize_bgr(
            image_bgr, round(w * scale), round(h * scale), upscale=False
        )
        return resized, scale
    return image_bgr, 1.0
