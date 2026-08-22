"""OCR 文本行合并与布局计算。"""

from __future__ import annotations

from typing import Iterable

from app.models import TextRegion
from services.ocr.base import OCRLine
from utils.language_utils import needs_translation


def filter_by_confidence(lines: list[OCRLine], min_confidence: float) -> list[OCRLine]:
    return [line for line in lines if line.confidence >= min_confidence]


def group_into_rows(lines: list[OCRLine], y_tolerance_ratio: float = 0.5) -> list[list[OCRLine]]:
    """按纵向重叠把文本行分组，返回按阅读顺序排列的行组。"""
    ordered = sorted(lines, key=lambda line: (line.box[1], line.box[0]))
    rows: list[list[OCRLine]] = []
    for line in ordered:
        _, top, _, height = line.box
        bottom = top + height
        placed = False
        for row in rows:
            row_top = min(item.box[1] for item in row)
            row_bottom = max(item.box[1] + item.box[3] for item in row)
            row_height = max(item.box[3] for item in row)
            overlap = min(row_bottom, bottom) - max(row_top, top)
            # 纵向重叠比例达到 (1-容差) 才视为同一行，避免把相邻两行并到一起
            required = (1.0 - y_tolerance_ratio) * min(height, row_height)
            if overlap >= required:
                row.append(line)
                placed = True
                break
        if not placed:
            rows.append([line])
    for row in rows:
        row.sort(key=lambda item: item.box[0])
    return rows


def merge_row_into_region(
    row: list[OCRLine], x_gap_ratio: float = 0.8
) -> tuple[tuple[int, int, int, int], str, float]:
    """同一行内的相邻文本合并成一个文本块。"""
    if not row:
        return (0, 0, 0, 0), "", 1.0
    merged = [row[0]]
    for line in row[1:]:
        prev = merged[-1]
        prev_x, prev_top, prev_w, prev_h = prev.box
        cur_x, cur_top, cur_w, cur_h = line.box
        prev_right = prev_x + prev_w
        gap = cur_x - prev_right
        char_width = max(4.0, max(prev_h, cur_h) * 0.6)
        if gap < x_gap_ratio * char_width:
            merged.append(line)
        else:
            yield _region_from_lines(merged)
            merged = [line]
    yield _region_from_lines(merged)


def _region_from_lines(lines: list[OCRLine]) -> tuple[tuple[int, int, int, int], str, float]:
    left = min(line.box[0] for line in lines)
    top = min(line.box[1] for line in lines)
    right = max(line.box[0] + line.box[2] for line in lines)
    bottom = max(line.box[1] + line.box[3] for line in lines)
    text = " ".join(line.text.strip() for line in lines if line.text.strip())
    confidence = min(line.confidence for line in lines)
    return (left, top, right - left, bottom - top), text, confidence


def merge_lines(
    lines: list[OCRLine],
    min_confidence: float = 0.6,
    y_tolerance_ratio: float = 0.5,
    x_gap_ratio: float = 0.8,
) -> list[tuple[tuple[int, int, int, int], str, float]]:
    """过滤 -> 分行 -> 合并，返回 (box, text, confidence) 列表。"""
    kept = filter_by_confidence(lines, min_confidence)
    result: list[tuple[tuple[int, int, int, int], str, float]] = []
    for row in group_into_rows(kept, y_tolerance_ratio):
        for region in merge_row_into_region(row, x_gap_ratio):
            result.append(region)
    return result


def ocr_lines_to_regions(
    lines: list[OCRLine],
    offset_x: int,
    offset_y: int,
    min_confidence: float = 0.6,
    y_tolerance_ratio: float = 0.5,
    x_gap_ratio: float = 0.8,
    screen_index: int = 0,
) -> list[TextRegion]:
    regions: list[TextRegion] = []
    for box, text, confidence in merge_lines(lines, min_confidence, y_tolerance_ratio, x_gap_ratio):
        if not text:
            continue
        x, y, w, h = box
        regions.append(
            TextRegion(
                text=text,
                x=offset_x + x,
                y=offset_y + y,
                width=w,
                height=h,
                source_line_height=float(h),
                confidence=confidence,
                screen_index=screen_index,
            )
        )
    return regions


def merge_wrapped_labels(
    regions: list[TextRegion], target_language: str, capture_width: int
) -> list[TextRegion]:
    """合并被 OCR 拆开的标签和段落，同时保留原始单行字号参考。

    只处理单列宽度内且左边缘对齐的相邻行，避免把表格不同列乱并。
    基准名称、文件名等不翻译内容也不会被吸进译文框。
    """
    if len(regions) < 2:
        return regions

    remaining = sorted(regions, key=lambda item: (item.y, item.x))
    max_label_width = max(180, int(round(max(1, capture_width) * 0.36)))
    merged: list[TextRegion] = []
    while remaining:
        current = remaining.pop(0)
        source_height = current.source_line_height or float(current.height)

        while remaining:
            match_index: int | None = None
            for candidate_index, candidate in enumerate(remaining):
                candidate_height = candidate.source_line_height or float(candidate.height)
                gap_tolerance = max(
                    8, int(round(max(source_height, candidate_height) * 0.8))
                )
                gap = candidate.y - current.bottom
                if gap > gap_tolerance:
                    break
                x_tolerance = max(
                    5, int(round(max(source_height, candidate_height) * 0.35))
                )
                if (
                    current.width <= max_label_width
                    and candidate.width <= max_label_width
                    and abs(candidate.x - current.x) <= x_tolerance
                    and 0 <= gap
                    and needs_translation(current.text, target_language)
                    and needs_translation(candidate.text, target_language)
                ):
                    match_index = candidate_index
                    break
            if match_index is None:
                break

            candidate = remaining.pop(match_index)
            candidate_height = candidate.source_line_height or float(candidate.height)
            right = max(current.right, candidate.right)
            bottom = max(current.bottom, candidate.bottom)
            current.text = f"{current.text.rstrip()} {candidate.text.lstrip()}"
            current.width = right - current.x
            current.height = bottom - current.y
            current.source_line_height = max(source_height, candidate_height)
            current.source_line_count += max(1, candidate.source_line_count)
            current.confidence = min(current.confidence, candidate.confidence)
            source_height = current.source_line_height

        merged.append(current)

    return sorted(merged, key=lambda item: (item.y, item.x))


def clamp_region(region: TextRegion, bounds: tuple[int, int, int, int]) -> TextRegion:
    """把区域限制在屏幕物理边界内，越界则平移 / 收缩。"""
    left, top, right, bottom = bounds
    x = max(left, min(region.x, right - 1))
    y = max(top, min(region.y, bottom - 1))
    x2 = max(x + 1, min(region.right, right))
    y2 = max(y + 1, min(region.bottom, bottom))
    region.x, region.y = x, y
    region.width, region.height = x2 - x, y2 - y
    return region
