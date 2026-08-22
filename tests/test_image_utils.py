"""OCR 预处理缩放测试（小图放大提升识别精度）。"""

from __future__ import annotations

import numpy as np

from utils.image_utils import resize_for_ocr, resize_to_max


def _img(width: int, height: int) -> np.ndarray:
    return np.zeros((height, width, 3), dtype=np.uint8)


def test_small_image_is_upscaled():
    img = _img(400, 300)
    resized, scale = resize_for_ocr(img)
    assert scale > 1.0
    assert resized.shape[1] > 400
    # 长边放大到接近 min_side（2200），受 max_upscale=3 限制
    assert max(resized.shape[:2]) <= 2200


def test_medium_image_unchanged():
    img = _img(2600, 1200)
    resized, scale = resize_for_ocr(img)
    assert scale == 1.0
    assert resized.shape == img.shape


def test_large_image_is_downscaled():
    img = _img(5000, 3000)
    resized, scale = resize_for_ocr(img)
    assert scale < 1.0
    assert max(resized.shape[:2]) <= 4096


def test_coordinates_restore_after_upscale():
    """放大后 OCR 框坐标除以 scale 应还原到原图坐标。"""
    img = _img(756, 445)
    _, scale = resize_for_ocr(img)
    scaled_x, scaled_y = 200, 150
    restored_x = round(scaled_x / scale)
    restored_y = round(scaled_y / scale)
    assert 0 <= restored_x < 756
    assert 0 <= restored_y < 445


def test_resize_to_max_still_works():
    img = _img(6000, 3000)
    resized, scale = resize_to_max(img, 4096)
    assert scale < 1.0
    assert max(resized.shape[:2]) <= 4096
