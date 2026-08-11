"""DPI / 多显示器坐标换算测试。"""

from utils.dpi_utils import (
    MonitorInfo,
    intersect,
    logical_rect_to_physical_rect,
    logical_rect_to_physical_union,
    physical_rect_to_local,
    physical_rect_to_overlay_geometry,
    physical_to_local_logical,
    union_rects,
)


def monitor(physical=(0, 0, 1920, 1080), logical=(0, 0), dpr=1.0):
    return MonitorInfo(index=0, physical=physical, logical_origin=logical, dpr=dpr)


def test_physical_to_local_logical_scaled():
    m = monitor(physical=(0, 0, 1920, 1080), logical=(0, 0), dpr=1.25)
    assert physical_to_local_logical(125, 250, m) == (100, 200)


def test_logical_rect_to_physical_rect_scaled():
    m = monitor(physical=(0, 0, 1920, 1080), logical=(0, 0), dpr=2.0)
    assert logical_rect_to_physical_rect((10, 20, 110, 120), m) == (20, 40, 220, 240)


def test_negative_coordinates_secondary_monitor():
    m = monitor(physical=(-1920, 0, 0, 1080), logical=(-1920, 0), dpr=1.0)
    assert physical_to_local_logical(-960, 540, m) == (960, 540)
    assert logical_rect_to_physical_rect((-1920, 0, -1000, 100), m) == (-1920, 0, -1000, 100)


def test_logical_rect_to_physical_union_multimonitor():
    primary = monitor(physical=(0, 0, 1920, 1080), logical=(0, 0), dpr=1.0)
    secondary = monitor(physical=(1920, 0, 3840, 1080), logical=(1920, 0), dpr=2.0)
    union = logical_rect_to_physical_union((1800, 0, 2020, 100), [primary, secondary])
    # 主屏部分 (1800,0,1920,100)，副屏部分逻辑 100px @dpr2 = 物理 200px
    assert union == (1800, 0, 2120, 200)


def test_physical_rect_to_overlay_geometry():
    m = monitor(physical=(0, 0, 1920, 1080), logical=(0, 0), dpr=1.25)
    geo = physical_rect_to_overlay_geometry((125, 250, 500, 400), m)
    assert geo == (100, 200, 300, 120)


def test_physical_rect_to_local():
    m = monitor(physical=(0, 0, 1920, 1080), logical=(0, 0), dpr=1.25)
    local = physical_rect_to_local((125, 250, 250, 300), (100, 200, 400, 500), m)
    assert local[0] == 20
    assert local[1] == 40
    assert local[2] == 100
    assert local[3] == 40


def test_intersect_and_union():
    assert intersect((0, 0, 10, 10), (5, 5, 20, 20)) == (5, 5, 10, 10)
    assert intersect((0, 0, 10, 10), (20, 20, 30, 30)) is None
    assert union_rects([(0, 0, 10, 10), (10, 0, 20, 10)]) == (0, 0, 20, 10)
