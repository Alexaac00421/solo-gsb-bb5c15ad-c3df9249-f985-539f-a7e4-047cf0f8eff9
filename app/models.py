"""ORM 模型：探头、读数、异常区间。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class Probe(Base):
    """已登记探头：温度上下限、最大采样间隔、最短异常时长均为固定配置。"""

    __tablename__ = "probes"

    probe_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    lower_limit: Mapped[float] = mapped_column(Float, nullable=False)
    upper_limit: Mapped[float] = mapped_column(Float, nullable=False)
    max_interval_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    min_duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    # 每接受一条新读数 +1；重传、拒绝均不变。
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class Reading(Base):
    """读数：同探头下来源编号唯一、采样时间唯一。"""

    __tablename__ = "readings"
    __table_args__ = (
        UniqueConstraint("probe_id", "source_reading_id", name="uq_readings_probe_source"),
        UniqueConstraint("probe_id", "sampled_at", name="uq_readings_probe_sampled_at"),
        Index("ix_readings_probe_sampled_at", "probe_id", "sampled_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    probe_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("probes.probe_id"), nullable=False
    )
    source_reading_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sampled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    temperature: Mapped[float] = mapped_column(Float, nullable=False)


class AnomalyInterval(Base):
    """异常区间：随探头修订号整体重建，revision 列标记其所属修订。"""

    __tablename__ = "anomaly_intervals"
    __table_args__ = (
        Index("ix_anomaly_intervals_probe_rev", "probe_id", "revision"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    probe_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("probes.probe_id"), nullable=False
    )
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    start_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
