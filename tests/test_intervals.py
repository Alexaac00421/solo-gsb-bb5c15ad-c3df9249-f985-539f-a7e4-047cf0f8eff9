"""区间计算规则单测。"""
from datetime import datetime, timedelta, timezone

from app.intervals import compute_intervals

T0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def rd(minutes: float, temp: float):
    return (T0 + timedelta(minutes=minutes), temp)


# 探头配置：[-2, 2]，最大间隔 10 分钟，最短异常时长 20 分钟
CFG = dict(min_temp=-2.0, max_temp=2.0, max_interval_seconds=600, min_duration_seconds=1200)


def test_continuous_oob_forms_interval():
    rs = [rd(0, 5), rd(10, 6), rd(20, 5), rd(30, 5)]  # 0..30 分钟越界，间隔均为 10 分钟
    iv = compute_intervals(rs, **CFG)
    assert iv == [(T0, T0 + timedelta(minutes=30))]


def test_duration_below_min_not_reported():
    rs = [rd(0, 5), rd(10, 5)]  # 时长 10 分钟 < 20 分钟
    assert compute_intervals(rs, **CFG) == []


def test_normal_reading_truncates():
    rs = [rd(0, 5), rd(10, 5), rd(20, 5), rd(30, 0), rd(40, 5), rd(50, 5), rd(60, 5)]
    iv = compute_intervals(rs, **CFG)
    assert iv == [
        (T0, T0 + timedelta(minutes=20)),
        (T0 + timedelta(minutes=40), T0 + timedelta(minutes=60)),
    ]


def test_long_gap_truncates():
    # 20 -> 40 间隔 20 分钟 > 10 分钟，截断为两个区间
    rs = [rd(0, 5), rd(10, 5), rd(20, 5), rd(40, 5), rd(50, 5), rd(60, 5)]
    iv = compute_intervals(rs, **CFG)
    assert iv == [
        (T0, T0 + timedelta(minutes=20)),
        (T0 + timedelta(minutes=40), T0 + timedelta(minutes=60)),
    ]


def test_gap_equal_to_limit_keeps_run():
    rs = [rd(0, 5), rd(10, 5), rd(20, 5)]  # 间隔恰好 10 分钟，不截断
    iv = compute_intervals(rs, **CFG)
    assert iv == [(T0, T0 + timedelta(minutes=20))]


def test_boundary_temps_are_normal():
    rs = [rd(0, -2.0), rd(5, 2.0), rd(10, 5)]
    assert compute_intervals(rs, **CFG) == []


def test_low_and_high_both_oob():
    rs = [rd(0, -5), rd(10, 9), rd(20, -8)]
    iv = compute_intervals(rs, **CFG)
    assert iv == [(T0, T0 + timedelta(minutes=20))]


def test_late_reading_merges_intervals():
    # 两段越界被超长间隔隔开
    early = [rd(0, 5), rd(10, 5), rd(20, 5), rd(40, 5), rd(50, 5), rd(60, 5)]
    iv1 = compute_intervals(early, **CFG)
    assert iv1 == [
        (T0, T0 + timedelta(minutes=20)),
        (T0 + timedelta(minutes=40), T0 + timedelta(minutes=60)),
    ]
    # 迟到读数（第 30 分钟越界）把两段连成一个区间
    late = sorted(early + [rd(30, 5)], key=lambda r: r[0])
    iv2 = compute_intervals(late, **CFG)
    assert iv2 == [(T0, T0 + timedelta(minutes=60))]


def test_single_oob_reading_zero_duration():
    assert compute_intervals([rd(0, 5)], **CFG) == []
