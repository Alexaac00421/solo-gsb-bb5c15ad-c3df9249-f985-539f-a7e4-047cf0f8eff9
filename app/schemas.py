"""请求/响应模型与参数校验。"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _require_tz_aware(v: datetime) -> datetime:
    if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
        raise ValueError("时间必须携带时区信息（如 2026-01-01T08:00:00+08:00 或 Z）")
    return v


class ProbeCreate(BaseModel):
    """登记探头：固定温度上下限、正数最大采样间隔、正数最短异常时长。"""

    probe_id: str = Field(min_length=1, max_length=128)
    min_temp: float = Field(allow_inf_nan=False)
    max_temp: float = Field(allow_inf_nan=False)
    max_interval_seconds: float = Field(gt=0, allow_inf_nan=False)
    min_duration_seconds: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _check_limits(self):
        if self.min_temp >= self.max_temp:
            raise ValueError("min_temp 必须小于 max_temp")
        return self


class ProbeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    probe_id: str
    min_temp: float
    max_temp: float
    max_interval_seconds: float
    min_duration_seconds: float
    revision: int


class ReadingIn(BaseModel):
    """上报读数：来源读数编号、带时区采样时间、温度。"""

    reading_id: str = Field(min_length=1, max_length=128)
    sampled_at: datetime
    temperature: float = Field(allow_inf_nan=False)

    @field_validator("sampled_at")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        return _require_tz_aware(v)


class ReadingAccepted(BaseModel):
    probe_id: str
    reading_id: str
    duplicate: bool  # True 表示同内容重传，幂等接受且修订号不变
    revision: int


class IntervalOut(BaseModel):
    start: datetime
    end: datetime


class AnomaliesOut(BaseModel):
    probe_id: str
    revision: int
    window_from: datetime
    window_to: datetime
    intervals: list[IntervalOut]
