"""DPI / 多显示器坐标换算。

坐标约定：
- 物理坐标：真实像素，全局（虚拟桌面）坐标系，可能为负，与 mss 一致。
- Qt 逻辑坐标：QScreen.geometry() 所在坐标系。
- 每个显示器维护 (physical rect, logical origin, dpr)，换算全部基于显示器局部偏移。
"""

from __future__ import annotations

import ctypes
import itertools
from dataclasses import dataclass
from typing import Sequence


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


_CALLBACK_FACTORY = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
_MONITOR_ENUM_PROC = _CALLBACK_FACTORY(
    ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_RECT), ctypes.c_void_p
)


def enum_display_monitors_physical() -> list[tuple[int, int, int, int]]:
    """枚举所有显示器的物理像素边界 (left, top, right, bottom)，可为负。"""
    rects: list[tuple[int, int, int, int]] = []

    def _cb(_hmon, _hdc, lprc, _data):
        r = lprc.contents
        rects.append((r.left, r.top, r.right, r.bottom))
        return 1

    try:
        ctypes.windll.user32.EnumDisplayMonitors(0, 0, _MONITOR_ENUM_PROC(_cb), 0)
    except Exception:
        pass
    if not rects:
        # 兜底：主屏 1920x1080
        rects.append((0, 0, 1920, 1080))
    return rects


@dataclass(frozen=True)
class MonitorInfo:
    index: int
    physical: tuple[int, int, int, int]  # l, t, r, b
    logical_origin: tuple[int, int]  # Qt 逻辑原点
    dpr: float

    @property
    def physical_origin(self) -> tuple[int, int]:
        return (self.physical[0], self.physical[1])


def _iou(a: tuple, b: tuple) -> float:
    inter = intersect(a, b)
    if inter is None:
        return 0.0
    ia = _area(inter)
    union = _area(a) + _area(b) - ia
    return ia / union if union > 0 else 0.0


def _area(r: tuple) -> int:
    return max(0, r[2] - r[0]) * max(0, r[3] - r[1])


def intersect(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> tuple | None:
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    if right <= left or bottom <= top:
        return None
    return (left, top, right, bottom)


def union_rects(rects: Sequence[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    left = min(r[0] for r in rects)
    top = min(r[1] for r in rects)
    right = max(r[2] for r in rects)
    bottom = max(r[3] for r in rects)
    return (left, top, right, bottom)


def build_monitor_map(screens, physical_monitors: list[tuple[int, int, int, int]]) -> list[MonitorInfo]:
    """把 Qt 的 QScreen 列表与物理显示器边界匹配起来。

    screens 元素需有 geometry() 和 devicePixelRatio()。
    """
    screens = list(screens)
    if not screens:
        return []
    physical_monitors = list(physical_monitors)
    if len(physical_monitors) < len(screens):
        # A hot-plug race or a non-Windows backend can expose fewer native
        # rectangles than Qt screens. Add per-screen inferred candidates so the
        # assignment can remain one-to-one instead of silently reusing a monitor.
        for screen in screens:
            geo = screen.geometry()
            dpr = float(screen.devicePixelRatio())
            inferred = (
                round(geo.x() * dpr),
                round(geo.y() * dpr),
                round((geo.x() + geo.width()) * dpr),
                round((geo.y() + geo.height()) * dpr),
            )
            if inferred not in physical_monitors:
                physical_monitors.append(inferred)

    logical_centers = [
        (screen.geometry().center().x(), screen.geometry().center().y()) for screen in screens
    ]
    physical_centers = [
        ((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)
        for rect in physical_monitors
    ]

    def _ranks(values: list[float]) -> list[int]:
        order = sorted(range(len(values)), key=lambda index: (values[index], index))
        ranks = [0] * len(values)
        for rank, index in enumerate(order):
            ranks[index] = rank
        return ranks

    logical_x_rank = _ranks([center[0] for center in logical_centers])
    logical_y_rank = _ranks([center[1] for center in logical_centers])
    physical_x_rank = _ranks([center[0] for center in physical_centers])
    physical_y_rank = _ranks([center[1] for center in physical_centers])

    def _pair_cost(screen_index: int, physical_index: int) -> float:
        screen = screens[screen_index]
        geo = screen.geometry()
        dpr = float(screen.devicePixelRatio())
        expected_width = max(1.0, geo.width() * dpr)
        expected_height = max(1.0, geo.height() * dpr)
        rect = physical_monitors[physical_index]
        width = max(1.0, rect[2] - rect[0])
        height = max(1.0, rect[3] - rect[1])
        size_cost = abs(width - expected_width) / expected_width + abs(
            height - expected_height
        ) / expected_height
        rank_cost = abs(logical_x_rank[screen_index] - physical_x_rank[physical_index])
        rank_cost += 0.35 * abs(
            logical_y_rank[screen_index] - physical_y_rank[physical_index]
        )
        expected = (
            round(geo.x() * dpr),
            round(geo.y() * dpr),
            round((geo.x() + geo.width()) * dpr),
            round((geo.y() + geo.height()) * dpr),
        )
        iou_bonus = _iou(expected, rect)
        origin_bonus = 0.0
        if geo.contains(0, 0) == (
            rect[0] <= 0 < rect[2] and rect[1] <= 0 < rect[3]
        ):
            origin_bonus = 0.5
        return size_cost * 8.0 + rank_cost * 1.5 - iou_bonus * 3.0 - origin_bonus

    # A physical monitor may back exactly one QScreen. Exhaustive assignment is
    # tiny in practice (usually 1–4 screens) and avoids duplicate mapping when
    # Win32 enumeration order differs from Qt, especially with mixed DPI.
    if len(physical_monitors) >= len(screens) and len(screens) <= 8:
        assignments = itertools.permutations(range(len(physical_monitors)), len(screens))
        best_assignment = min(
            assignments,
            key=lambda values: sum(
                _pair_cost(screen_index, physical_index)
                for screen_index, physical_index in enumerate(values)
            ),
        )
    else:
        best_assignment = tuple(
            min(
                range(len(physical_monitors)),
                key=lambda physical_index: _pair_cost(screen_index, physical_index),
            )
            for screen_index in range(len(screens))
        )

    result: list[MonitorInfo] = []
    for idx, screen in enumerate(screens):
        geo = screen.geometry()
        best_idx = best_assignment[idx]
        result.append(
            MonitorInfo(
                # OverlayManager groups by this key; it must remain unique even
                # if an operating-system monitor mapping is imperfect.
                index=idx,
                physical=physical_monitors[best_idx],
                logical_origin=(geo.x(), geo.y()),
                dpr=float(screen.devicePixelRatio()),
            )
        )
    return result


def physical_to_local_logical(
    px: int, py: int, monitor: MonitorInfo
) -> tuple[int, int]:
    """物理全局坐标 -> 该显示器上的 Qt 局部逻辑坐标（相对显示器逻辑原点）。"""
    ox, oy = monitor.physical_origin
    return (
        round((px - ox) / monitor.dpr),
        round((py - oy) / monitor.dpr),
    )


def local_logical_to_physical(
    lx: int, ly: int, monitor: MonitorInfo
) -> tuple[int, int]:
    """显示器局部逻辑坐标 -> 物理全局坐标。"""
    ox, oy = monitor.physical_origin
    return (
        ox + round(lx * monitor.dpr),
        oy + round(ly * monitor.dpr),
    )


def logical_rect_to_physical_rect(
    rect: tuple[int, int, int, int], monitor: MonitorInfo
) -> tuple[int, int, int, int]:
    """Qt 全局逻辑矩形 -> 物理全局矩形（仅限单个显示器内）。"""
    p1 = local_logical_to_physical(rect[0] - monitor.logical_origin[0], rect[1] - monitor.logical_origin[1], monitor)
    p2 = local_logical_to_physical(rect[2] - monitor.logical_origin[0], rect[3] - monitor.logical_origin[1], monitor)
    return (p1[0], p1[1], p2[0], p2[1])


def logical_rect_to_physical_union(
    rect: tuple[int, int, int, int], monitors: list[MonitorInfo]
) -> tuple | None:
    """Qt 全局逻辑矩形 -> 物理矩形并集（跨显示器时逐屏换算）。"""
    parts = logical_rect_to_physical_parts(rect, monitors)
    if not parts:
        return None
    return union_rects(parts)


def logical_rect_to_physical_parts(
    rect: tuple[int, int, int, int], monitors: list[MonitorInfo]
) -> list[tuple[int, int, int, int]]:
    """Map a global logical selection into independent per-monitor physical parts."""
    parts: list[tuple[int, int, int, int]] = []
    for monitor in monitors:
        ox, oy = monitor.logical_origin
        m_logical = (
            ox,
            oy,
            ox + round((monitor.physical[2] - monitor.physical[0]) / monitor.dpr),
            oy + round((monitor.physical[3] - monitor.physical[1]) / monitor.dpr),
        )
        inter = intersect(rect, m_logical)
        if inter is None:
            continue
        parts.append(logical_rect_to_physical_rect(inter, monitor))
    return parts


def parts_form_rectangle(parts: Sequence[tuple[int, int, int, int]]) -> bool:
    """Whether physical parts exactly tile their bounding rectangle without gaps."""
    if not parts:
        return False
    union = union_rects(parts)
    union_area = _area(union)
    # Monitor rectangles do not overlap in the supported desktop layout; equality
    # catches mixed-DPI/gapped selections that a single mss bbox cannot represent.
    return sum(_area(part) for part in parts) == union_area


def physical_rect_to_overlay_geometry(
    bbox: tuple[int, int, int, int], monitor: MonitorInfo
) -> tuple[int, int, int, int]:
    """物理截图矩形 -> Qt 全局逻辑 (x, y, w, h)，用于放置覆盖窗口。"""
    lx, ly = physical_to_local_logical(bbox[0], bbox[1], monitor)
    lx += monitor.logical_origin[0]
    ly += monitor.logical_origin[1]
    w = round((bbox[2] - bbox[0]) / monitor.dpr)
    h = round((bbox[3] - bbox[1]) / monitor.dpr)
    return (lx, ly, w, h)


def physical_rect_to_local(
    region_bbox: tuple[int, int, int, int],
    capture_bbox: tuple[int, int, int, int],
    monitor: MonitorInfo,
) -> tuple[int, int, int, int]:
    """物理区域 -> 覆盖窗口局部逻辑坐标 (x, y, w, h)。"""
    x = round((region_bbox[0] - capture_bbox[0]) / monitor.dpr)
    y = round((region_bbox[1] - capture_bbox[1]) / monitor.dpr)
    w = round((region_bbox[2] - region_bbox[0]) / monitor.dpr)
    h = round((region_bbox[3] - region_bbox[1]) / monitor.dpr)
    return (x, y, max(w, 1), max(h, 1))


def monitor_for_physical_point(
    px: int, py: int, monitors: list[MonitorInfo]
) -> MonitorInfo:
    for monitor in monitors:
        l, t, r, b = monitor.physical
        if l <= px < r and t <= py < b:
            return monitor
    # 不在任何显示器内（罕见）：按中心距离最近
    return min(
        monitors,
        key=lambda m: (
            (m.physical[0] + m.physical[2]) / 2 - px
        ) ** 2
        + ((m.physical[1] + m.physical[3]) / 2 - py) ** 2,
    )


def clamp_bbox_to_monitor(
    bbox: tuple[int, int, int, int], monitor: MonitorInfo
) -> tuple[int, int, int, int]:
    inter = intersect(bbox, monitor.physical)
    if inter is None:
        return monitor.physical
    return inter
