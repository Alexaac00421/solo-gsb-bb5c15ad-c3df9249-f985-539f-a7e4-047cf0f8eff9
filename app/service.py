"""核心业务逻辑。

并发与一致性设计：
- 每个写事务先对探头行 SELECT ... FOR UPDATE，同一探头的写入被串行化；
- 接受新读数后，在同一事务内递增修订号、全量重算并替换异常区间
  （迟到读数因此自然修正受影响区间），一次提交保证修订号与区间原子可见；
- 查询用单条 JOIN 语句同时取修订号与该修订号对应的区间，
  读已提交隔离级别下也不会出现区间与修订号不一致的快照。
"""
from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy import and_, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .intervals import as_utc, compute_intervals
from .models import AnomalyInterval, Probe, Reading
from .schemas import ProbeCreate, ReadingIn


class ProbeNotFound(Exception):
    def __init__(self, probe_id: str):
        self.probe_id = probe_id
        super().__init__(f"探头未登记: {probe_id}")


class ProbeAlreadyExists(Exception):
    def __init__(self, probe_id: str):
        self.probe_id = probe_id
        super().__init__(f"探头已登记: {probe_id}")


class ReadingConflict(Exception):
    pass


def register_probe(db: Session, payload: ProbeCreate) -> Probe:
    if db.get(Probe, payload.probe_id) is not None:
        raise ProbeAlreadyExists(payload.probe_id)
    probe = Probe(
        probe_id=payload.probe_id,
        min_temp=payload.min_temp,
        max_temp=payload.max_temp,
        max_interval_seconds=payload.max_interval_seconds,
        min_duration_seconds=payload.min_duration_seconds,
        revision=0,
    )
    db.add(probe)
    try:
        db.commit()
    except IntegrityError:  # 并发登记同一探头
        db.rollback()
        raise ProbeAlreadyExists(payload.probe_id)
    db.refresh(probe)
    return probe


def get_probe(db: Session, probe_id: str) -> Probe:
    probe = db.get(Probe, probe_id)
    if probe is None:
        raise ProbeNotFound(probe_id)
    return probe


def submit_reading(db: Session, probe_id: str, payload: ReadingIn) -> Tuple[bool, int]:
    """返回 (是否重复重传, 当前修订号)。"""
    # 行锁串行化同一探头的并发写入
    probe = db.execute(
        select(Probe).where(Probe.probe_id == probe_id).with_for_update()
    ).scalar_one_or_none()
    if probe is None:
        raise ProbeNotFound(probe_id)

    sampled_at = as_utc(payload.sampled_at)

    existing = db.execute(
        select(Reading).where(
            Reading.probe_id == probe_id, Reading.reading_id == payload.reading_id
        )
    ).scalar_one_or_none()
    if existing is not None:
        if as_utc(existing.sampled_at) == sampled_at and existing.temperature == payload.temperature:
            db.commit()  # 同内容重传：幂等接受，不递增修订号
            return True, probe.revision
        db.rollback()
        raise ReadingConflict(
            f"读数编号 {payload.reading_id} 已存在，但内容不一致"
        )

    clash = db.execute(
        select(Reading).where(
            Reading.probe_id == probe_id, Reading.sampled_at == sampled_at
        )
    ).scalar_one_or_none()
    if clash is not None:
        db.rollback()
        raise ReadingConflict(
            f"采样时间 {sampled_at.isoformat()} 已存在读数编号 {clash.reading_id}"
        )

    db.add(
        Reading(
            probe_id=probe_id,
            reading_id=payload.reading_id,
            sampled_at=sampled_at,
            temperature=payload.temperature,
        )
    )
    probe.revision += 1
    db.flush()

    rows = db.execute(
        select(Reading.sampled_at, Reading.temperature)
        .where(Reading.probe_id == probe_id)
        .order_by(Reading.sampled_at)
    ).all()
    intervals = compute_intervals(
        rows,
        probe.min_temp,
        probe.max_temp,
        probe.max_interval_seconds,
        probe.min_duration_seconds,
    )
    db.execute(delete(AnomalyInterval).where(AnomalyInterval.probe_id == probe_id))
    for start, end in intervals:
        db.add(
            AnomalyInterval(
                probe_id=probe_id,
                revision=probe.revision,
                start_at=start,
                end_at=end,
            )
        )
    try:
        db.commit()
    except IntegrityError as exc:  # 兜底：唯一约束冲突（如同采样时间并发插入）
        db.rollback()
        raise ReadingConflict("读数与已存在数据冲突") from exc
    return False, probe.revision


def get_anomalies(
    db: Session, probe_id: str, window_from: datetime, window_to: datetime
) -> Tuple[int, list[Tuple[datetime, datetime]]]:
    """单条语句取当前修订号及与时间窗相交的区间，保证二者一致。"""
    window_from = as_utc(window_from)
    window_to = as_utc(window_to)
    rows = db.execute(
        select(Probe.revision, AnomalyInterval.start_at, AnomalyInterval.end_at)
        .outerjoin(
            AnomalyInterval,
            and_(
                AnomalyInterval.probe_id == Probe.probe_id,
                AnomalyInterval.revision == Probe.revision,
                AnomalyInterval.start_at <= window_to,
                AnomalyInterval.end_at >= window_from,
            ),
        )
        .where(Probe.probe_id == probe_id)
        .order_by(AnomalyInterval.start_at)
    ).all()
    if not rows:
        raise ProbeNotFound(probe_id)
    revision = rows[0].revision
    intervals = [
        (as_utc(r.start_at), as_utc(r.end_at)) for r in rows if r.start_at is not None
    ]
    return revision, intervals
