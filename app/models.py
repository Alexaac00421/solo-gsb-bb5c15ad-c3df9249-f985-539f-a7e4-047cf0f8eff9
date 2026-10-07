"""ORM 模型。

probes            —— 探头登记信息与当前修订号
readings          —— 已接受的温度读数（幂等与冲突判定的依据）
anomaly_intervals —— 按修订号物化的异常区间，与修订号同事务提交，
                    保证查询看到的区间一定对应当前修订号
"""
from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# SQLite 仅 INTEGER PRIMARY KEY 支持自增；PostgreSQL 使用 BIGINT
AutoId = BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    pass


class Probe(Base):
    __tablename__ = "probes"

    probe_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    min_temp: Mapped[float] = mapped_column(Float, nullable=False)  # 温度下限
    max_temp: Mapped[float] = mapped_column(Float, nullable=False)  # 温度上限
    max_interval_seconds: Mapped[float] = mapped_column(Float, nullable=False)  # 最大采样间隔
    min_duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)  # 最短异常时长
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class Reading(Base):
    __tablename__ = "readings"
    __table_args__ = (
        # 兜底约束：同探头同采样时间只允许一条读数
        UniqueConstraint("probe_id", "sampled_at", name="uq_readings_probe_sampled_at"),
    )

    probe_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("probes.probe_id"), primary_key=True
    )
    reading_id: Mapped[str] = mapped_column(String(128), primary_key=True)  # 来源读数编号
    sampled_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), nullable=False)
    temperature: Mapped[float] = mapped_column(Float, nullable=False)


class AnomalyInterval(Base):
    __tablename__ = "anomaly_intervals"

    id: Mapped[int] = mapped_column(AutoId, primary_key=True, autoincrement=True)
    probe_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("probes.probe_id"), nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)  # 生成该区间时的探头修订号
    start_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), nullable=False)
