"""区间计算纯函数单元测试。"""
from datetime import datetime, timedelta, timezone

from app.intervals import ReadingPoint, compute_anomaly_intervals

T0 = datetime(2026, 10, 1, 0, 0, 0, tzinfo=timezone.utc)
LOWER, UPPER = -25.0, -18.0
MAX_GAP = 600.0      # 10 分钟
MIN_DUR = 900.0      # 15 分钟


def pt(minutes: float, temp: float) -> ReadingPoint:
    return ReadingPoint(sampled_at=T0 + timedelta(minutes=minutes), temperature=temp)


def test_empty_and_all_normal():
    assert compute_anomaly_intervals(
        [], lower_limit=LOWER, upper_limit=UPPER,
        max_interval_seconds=MAX_GAP, min_duration_seconds=MIN_DUR,
    ) == []
    pts = [pt(0, -20.0), pt(5, -21.0), pt(10, -19.0)]
    assert compute_anomaly_intervals(
        pts, lower_limit=LOWER, upper_limit=UPPER,
        max_interval_seconds=MAX_GAP, min_duration_seconds=MIN_DUR,
    ) == []


def test_temperature_exactly_on_limit_is_normal():
    # 恰好等于限值不算越界
    pts = [pt(0, -25.0), pt(5, -18.0), pt(10, -25.0)]
    assert compute_anomaly_intervals(
        pts, lower_limit=LOWER, upper_limit=UPPER,
        max_interval_seconds=MAX_GAP, min_duration_seconds=0.001,
    ) == []


def test_single_out_of_bounds_reading_never_forms_interval():
    # 单点时长为 0，达不到正数最短时长
    pts = [pt(0, -10.0)]
    assert compute_anomaly_intervals(
        pts, lower_limit=LOWER, upper_limit=UPPER,
        max_interval_seconds=MAX_GAP, min_duration_seconds=MIN_DUR,
    ) == []


def test_basic_interval_start_end_from_first_last_violation():
    pts = [
        pt(0, -20.0),            # 正常
        pt(5, -10.0),            # 越界（首）
        pt(10, -12.0),           # 越界
        pt(20, -15.0),           # 越界（末）
        pt(25, -20.0),           # 正常 → 截断
    ]
    ivs = compute_anomaly_intervals(
        pts, lower_limit=LOWER, upper_limit=UPPER,
        max_interval_seconds=MAX_GAP, min_duration_seconds=MIN_DUR,
    )
    assert len(ivs) == 1
    assert ivs[0].start == T0 + timedelta(minutes=5)
    assert ivs[0].end == T0 + timedelta(minutes=20)
    assert ivs[0].duration_seconds == 900.0


def test_duration_below_min_not_reported():
    pts = [pt(0, -10.0), pt(5, -10.0)]  # 时长 300s < 900s
    assert compute_anomaly_intervals(
        pts, lower_limit=LOWER, upper_limit=UPPER,
        max_interval_seconds=MAX_GAP, min_duration_seconds=MIN_DUR,
    ) == []


def test_gap_over_max_interval_splits():
    pts = [
        pt(0, -10.0), pt(5, -10.0),      # 区间 A（300s，不足）
        pt(20, -10.0), pt(30, -10.0),    # 间隔 15min > 10min → 新区间 B（600s，不足）
        pt(35, -10.0), pt(45, -10.0),    # 区间 B 延长（1500s，达标）
    ]
    ivs = compute_anomaly_intervals(
        pts, lower_limit=LOWER, upper_limit=UPPER,
        max_interval_seconds=MAX_GAP, min_duration_seconds=MIN_DUR,
    )
    assert len(ivs) == 1
    assert ivs[0].start == T0 + timedelta(minutes=20)
    assert ivs[0].end == T0 + timedelta(minutes=45)


def test_gap_exactly_max_interval_continues():
    pts = [pt(0, -10.0), pt(10, -10.0), pt(20, -10.0)]  # 间隔恰好 10min
    ivs = compute_anomaly_intervals(
        pts, lower_limit=LOWER, upper_limit=UPPER,
        max_interval_seconds=MAX_GAP, min_duration_seconds=MIN_DUR,
    )
    assert len(ivs) == 1
    assert ivs[0].duration_seconds == 1200.0


def test_normal_reading_truncates_even_within_gap():
    pts = [
        pt(0, -10.0), pt(5, -10.0),
        pt(8, -20.0),                      # 正常 → 截断
        pt(10, -10.0), pt(15, -10.0),      # 新区间（300s，不足）
    ]
    ivs = compute_anomaly_intervals(
        pts, lower_limit=LOWER, upper_limit=UPPER,
        max_interval_seconds=MAX_GAP, min_duration_seconds=MIN_DUR,
    )
    assert ivs == []


def test_unsorted_input_and_low_side_violation():
    pts = [pt(20, -30.0), pt(0, -30.0), pt(10, -30.0)]  # 乱序，低于下限
    ivs = compute_anomaly_intervals(
        pts, lower_limit=LOWER, upper_limit=UPPER,
        max_interval_seconds=MAX_GAP, min_duration_seconds=MIN_DUR,
    )
    assert len(ivs) == 1
    assert ivs[0].start == T0
    assert ivs[0].end == T0 + timedelta(minutes=20)


def test_late_reading_merges_two_runs():
    # 两段越界序列被 15min 缺口分开，各自时长不足
    early = [pt(0, -10.0), pt(5, -10.0), pt(20, -10.0), pt(25, -10.0)]
    ivs = compute_anomaly_intervals(
        early, lower_limit=LOWER, upper_limit=UPPER,
        max_interval_seconds=MAX_GAP, min_duration_seconds=MIN_DUR,
    )
    assert ivs == []
    late = early + [pt(10, -10.0), pt(15, -10.0)]  # 迟到读数填平缺口
    ivs = compute_anomaly_intervals(
        late, lower_limit=LOWER, upper_limit=UPPER,
        max_interval_seconds=MAX_GAP, min_duration_seconds=MIN_DUR,
    )
    assert len(ivs) == 1
    assert ivs[0].start == T0
    assert ivs[0].end == T0 + timedelta(minutes=25)
