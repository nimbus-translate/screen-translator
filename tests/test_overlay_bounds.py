"""覆盖层布局边界与文字颜色测试。"""

from app.models import TextRegion
from utils.image_utils import text_color_for_luminance
from utils.layout_utils import clamp_region


def test_auto_text_color_thresholds():
    assert text_color_for_luminance(0.9) == "#000000"
    assert text_color_for_luminance(0.1) == "#FFFFFF"


def test_region_clamped_on_all_sides():
    region = TextRegion(text="t", x=-20, y=500, width=300, height=100)
    clamp_region(region, (0, 0, 1920, 1080))
    assert region.x == 0
    assert region.width == 280
    assert region.bottom <= 1080


def test_tiny_region_keeps_minimum_size():
    region = TextRegion(text="t", x=100, y=100, width=1, height=1)
    clamp_region(region, (0, 0, 1920, 1080))
    assert region.width >= 1
    assert region.height >= 1
