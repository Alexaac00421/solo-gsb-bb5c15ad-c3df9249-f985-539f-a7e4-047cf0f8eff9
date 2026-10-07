"""异常区间计算（纯函数，便于单测）。

规则：
- 读数按采样时间升序处理；
- 温度低于下限或高于上限为越界；
- 连续越界读数构成一个区间，但相邻读数间隔超过最大采样间隔时截断；
- 正常读数同样截断当前区间；
- 区间起止取首、末越界读数的采样时间；
- 区间时长（末 - 首）达到最短异常时长才输出。
"""
from datetime import datetime, timezone
from typing import Iterable, Sequence, Tuple


def as_utc(t: datetime) -> datetime:
    """统一为 UTC  aware 时间；对无时区的值按 UTC 解释（SQLite 测试用）。"""
    if t.tzinfo is None or t.tzinfo.utcoffset(t) is None:
        return t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc)


def compute_intervals(
    readings: Iterable[Tuple[datetime, float]],
    min_temp: float,
    max_temp: float,
    max_interval_seconds: float,
    min_duration_seconds: float,
) -> list[Tuple[datetime, datetime]]:
    """readings 为 (采样时间, 温度) 序列，返回 [(区间起点, 区间终点), ...]。"""
    intervals: list[Tuple[datetime, datetime]] = []
    run_start: datetime | None = None
    run_end: datetime | None = None
    prev_at: datetime | None = None

    def close_run() -> None:
        nonlocal run_start, run_end
        if run_start is not None:
            if (run_end - run_start).total_seconds() >= min_duration_seconds:
                intervals.append((run_start, run_end))
            run_start = None
            run_end = None

    for sampled_at, temperature in readings:
        at = as_utc(sampled_at)
        if prev_at is not None and (at - prev_at).total_seconds() > max_interval_seconds:
            close_run()  # 相邻间隔超限，截断
        if temperature < min_temp or temperature > max_temp:
            if run_start is None:
                run_start = at
            run_end = at
        else:
            close_run()  # 正常读数截断
        prev_at = at
    close_run()
    return intervals
