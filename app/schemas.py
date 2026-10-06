"""请求/响应模型与参数校验。"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, List

from pydantic import AfterValidator, BaseModel, Field, FiniteFloat, model_validator


def _ensure_aware(v: datetime) -> datetime:
    if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
        raise ValueError("时间必须携带时区（ISO 8601，如 2026-10-06T08:00:00+08:00）")
    return v


AwareDateTime = Annotated[datetime, AfterValidator(_ensure_aware)]


class ProbeCreate(BaseModel):
    probe_id: str = Field(min_length=1, max_length=128)
    lower_limit: FiniteFloat
    upper_limit: FiniteFloat
    max_interval_seconds: FiniteFloat = Field(gt=0, description="正数，最大采样间隔（秒）")
    min_duration_seconds: FiniteFloat = Field(gt=0, description="正数，最短异常时长（秒）")

    @model_validator(mode="after")
    def _limits_ordered(self) -> "ProbeCreate":
        if not self.lower_limit < self.upper_limit:
            raise ValueError("lower_limit 必须小于 upper_limit")
        return self


class ProbeOut(BaseModel):
    probe_id: str
    lower_limit: float
    upper_limit: float
    max_interval_seconds: float
    min_duration_seconds: float
    revision: int


class ReadingIn(BaseModel):
    source_reading_id: str = Field(min_length=1, max_length=128)
    sampled_at: AwareDateTime
    temperature: FiniteFloat


class ReadingAck(BaseModel):
    probe_id: str
    source_reading_id: str
    status: str  # "accepted"（新读数）| "duplicate"（幂等重传）
    revision: int


class IntervalOut(BaseModel):
    start: datetime
    end: datetime
    duration_seconds: float


class AnomalyQueryResult(BaseModel):
    probe_id: str
    revision: int
    window_from: datetime
    window_to: datetime
    intervals: List[IntervalOut]
