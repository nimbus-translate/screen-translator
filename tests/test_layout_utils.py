"""文本区域合并与置信度过滤测试。"""

from app.models import TextRegion
from services.ocr.base import OCRLine
from utils.layout_utils import (
    clamp_region,
    filter_by_confidence,
    group_into_rows,
    merge_lines,
    ocr_lines_to_regions,
)


def make_line(text, x, y, w, h, confidence=0.9):
    return OCRLine(text=text, box=(x, y, w, h), confidence=confidence)


def test_filter_by_confidence():
    lines = [
        make_line("ok", 0, 0, 10, 10, confidence=0.9),
        make_line("no", 0, 0, 10, 10, confidence=0.4),
    ]
    kept = filter_by_confidence(lines, 0.6)
    assert len(kept) == 1
    assert kept[0].text == "ok"


def test_group_into_rows_separates_lines():
    lines = [
        make_line("上", 0, 0, 20, 20),
        make_line("下", 0, 100, 20, 20),
    ]
    rows = group_into_rows(lines, y_tolerance_ratio=0.5)
    assert len(rows) == 2


def test_group_into_rows_joins_same_row():
    lines = [
        make_line("A", 0, 0, 20, 20),
        make_line("B", 30, 2, 20, 18),
    ]
    rows = group_into_rows(lines, y_tolerance_ratio=0.5)
    assert len(rows) == 1
    assert len(rows[0]) == 2


def test_merge_lines_joins_adjacent_but_not_far():
    adjacent = [
        make_line("hello", 0, 0, 60, 20),
        make_line("world", 65, 0, 50, 20),
    ]
    merged = merge_lines(adjacent, min_confidence=0.0, x_gap_ratio=0.8)
    assert len(merged) == 1
    assert merged[0][1] == "hello world"

    far = [
        make_line("left", 0, 0, 60, 20),
        make_line("right", 400, 0, 50, 20),
    ]
    merged_far = merge_lines(far, min_confidence=0.0, x_gap_ratio=0.8)
    assert len(merged_far) == 2


def test_ocr_lines_to_regions_offsets():
    lines = [make_line("x", 10, 20, 30, 15)]
    regions = ocr_lines_to_regions(lines, offset_x=1920, offset_y=100, min_confidence=0.0)
    assert regions[0].x == 1930
    assert regions[0].y == 120
    assert regions[0].width == 30


def test_clamp_region_stays_inside_bounds():
    region = TextRegion(text="t", x=10, y=10, width=100, height=50)
    clamp_region(region, (0, 0, 120, 80))
    assert region.right <= 120
    assert region.bottom <= 80

    region2 = TextRegion(text="t", x=-50, y=-50, width=100, height=50)
    clamp_region(region2, (0, 0, 120, 80))
    assert region2.x >= 0
    assert region2.y >= 0
