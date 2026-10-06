"""HTTP API 入口。"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from . import service
from .db import Base, engine, get_db
from .models import Probe
from .schemas import (
    AnomalyQueryResult,
    IntervalOut,
    ProbeCreate,
    ProbeOut,
    ReadingAck,
    ReadingIn,
)

logger = logging.getLogger("coldchain")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时建表（数据库初始化），等待数据库就绪。
    last_err: Exception | None = None
    for _ in range(60):
        try:
            Base.metadata.create_all(engine)
            logger.info("数据库初始化完成")
            break
        except OperationalError as exc:
            last_err = exc
            time.sleep(1)
    else:
        raise RuntimeError(f"数据库初始化失败: {last_err}")
    yield


app = FastAPI(
    title="冷库温度异常区间服务",
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(service.ProbeNotFoundError)
async def probe_not_found_handler(request: Request, exc: service.ProbeNotFoundError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(service.ProbeConflictError)
async def probe_conflict_handler(request: Request, exc: service.ProbeConflictError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(service.ReadingConflictError)
async def reading_conflict_handler(request: Request, exc: service.ReadingConflictError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.get("/")
def root():
    return {"service": "冷库温度异常区间服务", "docs": "/docs"}


@app.post("/probes", response_model=ProbeOut, status_code=201)
def create_probe(
    payload: ProbeCreate, response: Response, db: Session = Depends(get_db)
):
    """登记探头：固定温度上下限、正数最大采样间隔、正数最短异常时长。"""
    probe, created = service.register_probe(db, payload)
    if not created:
        response.status_code = 200  # 同配置重复登记：幂等
    return ProbeOut(
        probe_id=probe.probe_id,
        lower_limit=probe.lower_limit,
        upper_limit=probe.upper_limit,
        max_interval_seconds=probe.max_interval_seconds,
        min_duration_seconds=probe.min_duration_seconds,
        revision=probe.revision,
    )


@app.get("/probes/{probe_id}", response_model=ProbeOut)
def get_probe(probe_id: str, db: Session = Depends(get_db)):
    probe = db.get(Probe, probe_id)
    if probe is None:
        raise service.ProbeNotFoundError(f"探头 {probe_id} 未登记")
    return ProbeOut(
        probe_id=probe.probe_id,
        lower_limit=probe.lower_limit,
        upper_limit=probe.upper_limit,
        max_interval_seconds=probe.max_interval_seconds,
        min_duration_seconds=probe.min_duration_seconds,
        revision=probe.revision,
    )


@app.post("/probes/{probe_id}/readings", response_model=ReadingAck, status_code=201)
def submit_reading(
    probe_id: str,
    payload: ReadingIn,
    response: Response,
    db: Session = Depends(get_db),
):
    """上报读数。

    - 201 accepted：新读数被接受，修订号 +1；
    - 200 duplicate：同编号同内容重传，幂等，修订号不变；
    - 404：探头未登记；409：内容冲突或采样时间冲突；422：参数无效。
    """
    status, revision = service.ingest_reading(db, probe_id, payload)
    if status == "duplicate":
        response.status_code = 200
    return ReadingAck(
        probe_id=probe_id,
        source_reading_id=payload.source_reading_id,
        status=status,
        revision=revision,
    )


@app.get("/probes/{probe_id}/anomalies", response_model=AnomalyQueryResult)
def get_anomalies(
    probe_id: str,
    window_from: datetime = Query(alias="from", description="时间窗起点，须带时区"),
    window_to: datetime = Query(alias="to", description="时间窗终点，须带时区"),
    db: Session = Depends(get_db),
):
    """查询指定探头与时间窗相交的当前异常区间及修订号。"""
    for name, v in (("from", window_from), ("to", window_to)):
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise HTTPException(status_code=422, detail=f"查询参数 {name} 必须携带时区")
    if window_from > window_to:
        raise HTTPException(status_code=422, detail="from 必须早于或等于 to")
    revision, intervals = service.query_anomalies(db, probe_id, window_from, window_to)
    return AnomalyQueryResult(
        probe_id=probe_id,
        revision=revision,
        window_from=window_from,
        window_to=window_to,
        intervals=[
            IntervalOut(
                start=s, end=e, duration_seconds=(e - s).total_seconds()
            )
            for s, e in intervals
        ],
    )
