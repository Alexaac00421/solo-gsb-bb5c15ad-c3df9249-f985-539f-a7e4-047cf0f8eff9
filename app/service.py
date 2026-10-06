"""核心业务逻辑：探头登记、读数摄入（幂等/冲突/区间重建）、异常查询。

一致性设计：
- 摄入读数时对探头行 SELECT ... FOR UPDATE，串行化同一探头的并发写入；
- 接受新读数 → 修订号 +1 → 基于该探头全部读数重算异常区间 →
  与旧区间替换在同一事务内提交，因此 (revision, intervals) 永远原子一致；
- 迟到读数只是参与全量重算，天然修正受影响区间（合并/拆分/延长/消失）；
- 查询用单条 SQL（探头 LEFT JOIN 区间且 revision 相等），
  单语句快照保证不会出现与修订号不一致的区间。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Tuple

from sqlalchemy import and_, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .intervals import ReadingPoint, compute_anomaly_intervals
from .models import AnomalyInterval, Probe, Reading
from .schemas import ProbeCreate, ReadingIn


class ProbeNotFoundError(Exception):
    """探头未登记。"""


class ProbeConflictError(Exception):
    """探头已存在且配置不一致。"""


class ReadingConflictError(Exception):
    """读数冲突：同编号内容不一致，或同采样时间编号不同。"""


def _aware(dt: datetime) -> datetime:
    """防御性归一化：PostgreSQL timestamptz 读回即为带时区时间。"""
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def register_probe(db: Session, payload: ProbeCreate) -> Tuple[Probe, bool]:
    """登记探头。返回 (probe, created)。

    同配置重复登记幂等；配置不一致返回冲突。
    """
    probe = db.get(Probe, payload.probe_id)
    if probe is not None:
        _assert_same_config(probe, payload)
        return probe, False

    probe = Probe(
        probe_id=payload.probe_id,
        lower_limit=payload.lower_limit,
        upper_limit=payload.upper_limit,
        max_interval_seconds=payload.max_interval_seconds,
        min_duration_seconds=payload.min_duration_seconds,
        revision=0,
    )
    db.add(probe)
    try:
        db.commit()
    except IntegrityError:
        # 并发登记同一探头：回滚后按已存在处理。
        db.rollback()
        probe = db.get(Probe, payload.probe_id)
        _assert_same_config(probe, payload)
        return probe, False
    return probe, True


def _assert_same_config(probe: Probe, payload: ProbeCreate) -> None:
    same = (
        probe.lower_limit == payload.lower_limit
        and probe.upper_limit == payload.upper_limit
        and probe.max_interval_seconds == payload.max_interval_seconds
        and probe.min_duration_seconds == payload.min_duration_seconds
    )
    if not same:
        raise ProbeConflictError(
            f"探头 {probe.probe_id} 已登记且配置不同，登记配置不可变更"
        )


def ingest_reading(db: Session, probe_id: str, payload: ReadingIn) -> Tuple[str, int]:
    """摄入一条读数。返回 (status, revision)，status ∈ {accepted, duplicate}。"""
    # 行锁串行化同探头并发写入，保证修订号与区间严格一致。
    probe = db.execute(
        select(Probe).where(Probe.probe_id == probe_id).with_for_update()
    ).scalar_one_or_none()
    if probe is None:
        raise ProbeNotFoundError(f"探头 {probe_id} 未登记")

    # 同探头同来源编号：内容一致 → 幂等；不一致 → 拒绝。
    existing = db.execute(
        select(Reading).where(
            Reading.probe_id == probe_id,
            Reading.source_reading_id == payload.source_reading_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        if (
            _aware(existing.sampled_at) == payload.sampled_at
            and existing.temperature == payload.temperature
        ):
            db.rollback()  # 释放行锁；重传不递增修订号
            return "duplicate", probe.revision
        raise ReadingConflictError(
            f"读数编号 {payload.source_reading_id} 已存在，但采样时间或温度不一致"
        )

    # 同探头同采样时间但编号不同 → 拒绝。
    clash = db.execute(
        select(Reading).where(
            Reading.probe_id == probe_id,
            Reading.sampled_at == payload.sampled_at,
        )
    ).scalar_one_or_none()
    if clash is not None:
        raise ReadingConflictError(
            f"采样时间 {payload.sampled_at.isoformat()} 已被读数 "
            f"{clash.source_reading_id} 占用"
        )

    db.add(
        Reading(
            probe_id=probe_id,
            source_reading_id=payload.source_reading_id,
            sampled_at=payload.sampled_at,
            temperature=payload.temperature,
        )
    )
    probe.revision += 1
    db.flush()

    points = [
        ReadingPoint(sampled_at=_aware(r.sampled_at), temperature=r.temperature)
        for r in db.execute(
            select(Reading)
            .where(Reading.probe_id == probe_id)
            .order_by(Reading.sampled_at)
        ).scalars()
    ]
    intervals = compute_anomaly_intervals(
        points,
        lower_limit=probe.lower_limit,
        upper_limit=probe.upper_limit,
        max_interval_seconds=probe.max_interval_seconds,
        min_duration_seconds=probe.min_duration_seconds,
    )
    db.execute(delete(AnomalyInterval).where(AnomalyInterval.probe_id == probe_id))
    for iv in intervals:
        db.add(
            AnomalyInterval(
                probe_id=probe_id,
                revision=probe.revision,
                start_ts=iv.start,
                end_ts=iv.end,
            )
        )
    db.commit()
    return "accepted", probe.revision


def query_anomalies(
    db: Session, probe_id: str, window_from: datetime, window_to: datetime
) -> Tuple[int, List[Tuple[datetime, datetime]]]:
    """查询与 [window_from, window_to] 相交的当前异常区间及修订号。

    单条 SQL 完成，保证 revision 与区间取自同一快照。
    """
    stmt = (
        select(Probe.revision, AnomalyInterval.start_ts, AnomalyInterval.end_ts)
        .outerjoin(
            AnomalyInterval,
            and_(
                AnomalyInterval.probe_id == Probe.probe_id,
                AnomalyInterval.revision == Probe.revision,
                AnomalyInterval.start_ts <= window_to,
                AnomalyInterval.end_ts >= window_from,
            ),
        )
        .where(Probe.probe_id == probe_id)
        .order_by(AnomalyInterval.start_ts)
    )
    rows = db.execute(stmt).all()
    if not rows:
        raise ProbeNotFoundError(f"探头 {probe_id} 未登记")
    revision = rows[0][0]
    intervals = [(_aware(s), _aware(e)) for _, s, e in rows if s is not None]
    return revision, intervals
