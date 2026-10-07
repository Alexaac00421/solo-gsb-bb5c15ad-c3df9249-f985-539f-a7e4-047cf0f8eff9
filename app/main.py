"""HTTP API 入口。"""
import time
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from . import config
from .database import engine, get_db
from .models import Base
from .schemas import (
    AnomaliesOut,
    IntervalOut,
    ProbeCreate,
    ProbeOut,
    ReadingAccepted,
    ReadingIn,
)
from .service import (
    ProbeAlreadyExists,
    ProbeNotFound,
    ReadingConflict,
    get_anomalies,
    get_probe,
    register_probe,
    submit_reading,
)


def _init_db() -> None:
    """启动时建表（数据库初始化），并等待数据库就绪。"""
    last_exc: Exception | None = None
    for _ in range(config.DB_CONNECT_RETRIES):
        try:
            Base.metadata.create_all(engine)
            return
        except OperationalError as exc:
            last_exc = exc
            time.sleep(config.DB_CONNECT_RETRY_INTERVAL)
    raise RuntimeError("数据库连接失败") from last_exc


@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_db()
    yield


app = FastAPI(title="冷库温度异常区间服务", version="1.0.0", lifespan=lifespan)


@app.exception_handler(ProbeNotFound)
def _not_found(_: Request, exc: ProbeNotFound):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(ProbeAlreadyExists)
def _already_exists(_: Request, exc: ProbeAlreadyExists):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(ReadingConflict)
def _conflict(_: Request, exc: ReadingConflict):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/probes", response_model=ProbeOut, status_code=201)
def create_probe(payload: ProbeCreate, db: Session = Depends(get_db)):
    """登记探头：固定温度上下限、正数最大采样间隔、正数最短异常时长。"""
    return register_probe(db, payload)


@app.get("/probes/{probe_id}", response_model=ProbeOut)
def read_probe(probe_id: str, db: Session = Depends(get_db)):
    return get_probe(db, probe_id)


@app.post("/probes/{probe_id}/readings", response_model=ReadingAccepted)
def create_reading(probe_id: str, payload: ReadingIn, db: Session = Depends(get_db)):
    """上报读数。同探头同编号同内容重传幂等；内容冲突或同采样时间不同编号返回 409。"""
    duplicate, revision = submit_reading(db, probe_id, payload)
    return ReadingAccepted(
        probe_id=probe_id,
        reading_id=payload.reading_id,
        duplicate=duplicate,
        revision=revision,
    )


@app.get("/probes/{probe_id}/anomalies", response_model=AnomaliesOut)
def list_anomalies(
    probe_id: str,
    window_from: datetime = Query(alias="from"),
    window_to: datetime = Query(alias="to"),
    db: Session = Depends(get_db),
):
    """查询与有效时间窗 [from, to] 相交的当前异常区间及修订号。"""
    for name, v in (("from", window_from), ("to", window_to)):
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise RequestValidationError(
                errors=[{"loc": ["query", name], "msg": "时间必须携带时区信息", "type": "value_error"}]
            )
    if window_from >= window_to:
        raise RequestValidationError(
            errors=[{"loc": ["query", "from"], "msg": "from 必须早于 to", "type": "value_error"}]
        )
    revision, intervals = get_anomalies(db, probe_id, window_from, window_to)
    return AnomaliesOut(
        probe_id=probe_id,
        revision=revision,
        window_from=window_from,
        window_to=window_to,
        intervals=[IntervalOut(start=s, end=e) for s, e in intervals],
    )
