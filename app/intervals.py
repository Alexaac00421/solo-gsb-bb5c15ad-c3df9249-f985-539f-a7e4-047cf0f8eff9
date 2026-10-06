"""异常区间计算：纯函数实现，便于单元测试。

规则：
- 温度严格低于下限或严格高于上限为越界（恰好等于限值视为正常）；
- 按采样时间升序扫描，连续越界读数构成候选区间；
- 相邻读数间隔超过 max_interval_seconds 时截断当前候选区间；
- 正常读数截断当前候选区间；
- 区间起点/终点取首/末越界读数的采样时间；
- 区间时长（终点 - 起点）达到 min_duration_seconds 才输出为异常。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List, Optional


@dataclass(frozen=True)
class ReadingPoint:
    sampled_at: datetime
    temperature: float


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime

    @property
    def duration_seconds(self) -> float:
        return (self.end - self.start).total_seconds()


def compute_anomaly_intervals(
    points: Iterable[ReadingPoint],
    *,
    lower_limit: float,
    upper_limit: float,
    max_interval_seconds: float,
    min_duration_seconds: float,
) -> List[Interval]:
    ordered = sorted(points, key=lambda p: p.sampled_at)
    intervals: List[Interval] = []
    run_start: Optional[datetime] = None
    run_end: Optional[datetime] = None
    prev_at: Optional[datetime] = None

    def close_run() -> None:
        nonlocal run_start, run_end
        if run_start is not None and run_end is not None:
            if (run_end - run_start).total_seconds() >= min_duration_seconds:
                intervals.append(Interval(run_start, run_end))
        run_start = None
        run_end = None

    for p in ordered:
        out_of_bounds = p.temperature < lower_limit or p.temperature > upper_limit
        if out_of_bounds:
            if run_start is not None:
                # run 存续期间上一条读数必然越界，prev_at 即相邻前序读数。
                gap = (p.sampled_at - prev_at).total_seconds()
                if gap > max_interval_seconds:
                    close_run()
            if run_start is None:
                run_start = p.sampled_at
            run_end = p.sampled_at
        else:
            close_run()
        prev_at = p.sampled_at
    close_run()
    return intervals
