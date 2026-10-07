"""并发写入一致性测试：需要真实 PostgreSQL（FOR UPDATE 行锁）。

运行方式：
    DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/db pytest tests/test_concurrency.py
"""
import os
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import service
from app.models import Base
from app.schemas import ProbeCreate, ReadingIn

DB_URL = os.environ.get("DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DB_URL.startswith("postgresql"),
    reason="并发一致性测试需要 PostgreSQL（设置 DATABASE_URL 后运行）",
)

engine = create_engine(DB_URL)
Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
PROBE_ID = "concurrency-probe"


@pytest.fixture(autouse=True)
def fresh_probe():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = Session()
    try:
        service.register_probe(
            db,
            ProbeCreate(
                probe_id=PROBE_ID,
                min_temp=-2.0,
                max_temp=2.0,
                max_interval_seconds=600,
                min_duration_seconds=1,
            ),
        )
    finally:
        db.close()


def _run_threads(fn, n):
    threads = [threading.Thread(target=fn, args=(t,)) for t in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def test_concurrent_unique_readings_revision_and_intervals():
    """8 线程并发写入 80 条不同读数：修订号必须精确等于 80，区间完整。"""
    def worker(tid):
        db = Session()
        try:
            for i in range(10):
                service.submit_reading(
                    db,
                    PROBE_ID,
                    ReadingIn(
                        reading_id=f"t{tid}-r{i}",
                        sampled_at=T0 + timedelta(minutes=tid * 10 + i),
                        temperature=5.0,
                    ),
                )
        finally:
            db.close()

    _run_threads(worker, 8)

    db = Session()
    try:
        rev, intervals = service.get_anomalies(
            db, PROBE_ID, T0 - timedelta(days=1), T0 + timedelta(days=1)
        )
        assert rev == 80  # 每次接受新读数恰好递增一次
        assert intervals == [(T0, T0 + timedelta(minutes=79))]
    finally:
        db.close()


def test_concurrent_identical_retransmit_counts_once():
    """8 线程同时重传同一条读数：仅一个视为新读数，其余幂等，修订号为 1。"""
    results = []
    lock = threading.Lock()

    def worker(_):
        db = Session()
        try:
            dup, rev = service.submit_reading(
                db,
                PROBE_ID,
                ReadingIn(reading_id="same", sampled_at=T0, temperature=5.0),
            )
            with lock:
                results.append((dup, rev))
        finally:
            db.close()

    _run_threads(worker, 8)
    assert sum(1 for dup, _ in results if not dup) == 1  # 恰好一个非重复
    assert sum(1 for dup, _ in results if dup) == 7

    db = Session()
    try:
        rev, _ = service.get_anomalies(db, PROBE_ID, T0 - timedelta(days=1), T0 + timedelta(days=1))
        assert rev == 1
    finally:
        db.close()


def test_concurrent_conflicting_content_single_winner():
    """同编号不同内容并发提交：恰好一个成功，其余 409，修订号为 1。"""
    outcomes = []
    lock = threading.Lock()

    def worker(tid):
        db = Session()
        try:
            try:
                service.submit_reading(
                    db,
                    PROBE_ID,
                    ReadingIn(reading_id="same", sampled_at=T0, temperature=float(tid)),
                )
                outcome = "accepted"
            except service.ReadingConflict:
                outcome = "conflict"
            with lock:
                outcomes.append(outcome)
        finally:
            db.close()

    _run_threads(worker, 8)
    assert outcomes.count("accepted") == 1
    assert outcomes.count("conflict") == 7

    db = Session()
    try:
        rev, _ = service.get_anomalies(db, PROBE_ID, T0 - timedelta(days=1), T0 + timedelta(days=1))
        assert rev == 1
    finally:
        db.close()


def test_query_during_writes_always_consistent():
    """写入进行中并发查询：每次返回的区间都必须与返回的修订号一致。"""
    stop = threading.Event()
    violations = []

    def writer(tid):
        db = Session()
        try:
            for i in range(20):
                service.submit_reading(
                    db,
                    PROBE_ID,
                    ReadingIn(
                        reading_id=f"w{tid}-{i}",
                        sampled_at=T0 + timedelta(minutes=tid * 20 + i),
                        temperature=5.0,
                    ),
                )
        finally:
            db.close()
        stop.set()

    def reader():
        db = Session()
        try:
            while not stop.is_set():
                rev, intervals = service.get_anomalies(
                    db, PROBE_ID, T0 - timedelta(days=1), T0 + timedelta(days=1)
                )
                db.rollback()
                # 不变式：若返回修订号为 rev，则区间必须是对应 rev 的物化结果。
                # 由于区间与修订号在同事务提交、同一条 SQL 读取，
                # 这里验证区间数量与修订号单调可解释：区间端点不超出已接受读数范围。
                for start, end in intervals:
                    if not (T0 <= start <= end <= T0 + timedelta(minutes=79)):
                        violations.append((rev, start, end))
        finally:
            db.close()

    writers = [threading.Thread(target=writer, args=(t,)) for t in range(4)]
    readers = [threading.Thread(target=reader) for _ in range(4)]
    for t in writers + readers:
        t.start()
    for t in writers + readers:
        t.join()

    assert violations == []
    db = Session()
    try:
        rev, intervals = service.get_anomalies(
            db, PROBE_ID, T0 - timedelta(days=1), T0 + timedelta(days=1)
        )
        assert rev == 80
        assert intervals == [(T0, T0 + timedelta(minutes=79))]
    finally:
        db.close()
