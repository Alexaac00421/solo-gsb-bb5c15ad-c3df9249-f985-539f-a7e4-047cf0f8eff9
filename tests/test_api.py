"""API 集成测试（SQLite 内存库 + FastAPI TestClient）。"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import Base

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def override_get_db():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


PROBE = {
    "probe_id": "cold-room-1",
    "min_temp": -2.0,
    "max_temp": 2.0,
    "max_interval_seconds": 600,
    "min_duration_seconds": 1200,
}


def register(**overrides):
    body = {**PROBE, **overrides}
    return client.post("/probes", json=body)


def submit(reading_id, sampled_at, temperature, probe_id="cold-room-1"):
    return client.post(
        f"/probes/{probe_id}/readings",
        json={"reading_id": reading_id, "sampled_at": sampled_at, "temperature": temperature},
    )


def anomalies(probe_id="cold-room-1", frm="2026-01-01T00:00:00Z", to="2026-01-02T00:00:00Z"):
    return client.get(f"/probes/{probe_id}/anomalies", params={"from": frm, "to": to})


def test_register_probe():
    r = register()
    assert r.status_code == 201
    assert r.json()["revision"] == 0
    assert register().status_code == 409  # 重复登记


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_interval_seconds", 0),
        ("max_interval_seconds", -5),
        ("min_duration_seconds", 0),
        ("min_duration_seconds", -1),
    ],
)
def test_register_non_positive_params_rejected(field, value):
    assert register(**{field: value}).status_code == 422


def test_register_inverted_limits_rejected():
    assert register(min_temp=5.0, max_temp=-5.0).status_code == 422


def test_reading_for_unknown_probe_404():
    assert submit("r1", "2026-01-01T00:00:00Z", 5.0, probe_id="ghost").status_code == 404


def test_naive_sampled_at_rejected():
    register()
    assert submit("r1", "2026-01-01T00:00:00", 5.0).status_code == 422


def test_interval_forms_and_revision_increments():
    register()
    assert submit("r1", "2026-01-01T00:00:00Z", 5.0).json()["revision"] == 1
    assert submit("r2", "2026-01-01T00:10:00Z", 6.0).json()["revision"] == 2
    r = submit("r3", "2026-01-01T00:20:00Z", 5.0)
    assert r.json() == {
        "probe_id": "cold-room-1",
        "reading_id": "r3",
        "duplicate": False,
        "revision": 3,
    }
    body = anomalies().json()
    assert body["revision"] == 3
    assert body["intervals"] == [
        {"start": "2026-01-01T00:00:00Z", "end": "2026-01-01T00:20:00Z"}
    ]


def test_idempotent_retransmit_no_revision_bump():
    register()
    submit("r1", "2026-01-01T00:00:00Z", 5.0)
    r = submit("r1", "2026-01-01T00:00:00Z", 5.0)  # 同探头同编号同内容重传
    assert r.status_code == 200
    assert r.json()["duplicate"] is True
    assert r.json()["revision"] == 1
    assert anomalies().json()["revision"] == 1


def test_same_id_different_content_rejected():
    register()
    submit("r1", "2026-01-01T00:00:00Z", 5.0)
    assert submit("r1", "2026-01-01T00:00:00Z", 6.0).status_code == 409
    assert submit("r1", "2026-01-01T00:05:00Z", 5.0).status_code == 409
    assert anomalies().json()["revision"] == 1  # 冲突不影响修订号


def test_same_time_different_id_rejected():
    register()
    submit("r1", "2026-01-01T00:00:00Z", 5.0)
    r = submit("r2", "2026-01-01T00:00:00Z", 5.0)
    assert r.status_code == 409
    assert anomalies().json()["revision"] == 1


def test_late_reading_corrects_intervals():
    register()
    submit("r1", "2026-01-01T00:00:00Z", 5.0)
    submit("r2", "2026-01-01T00:10:00Z", 5.0)
    submit("r3", "2026-01-01T00:20:00Z", 5.0)
    submit("r4", "2026-01-01T00:30:00Z", 5.0)
    assert len(anomalies().json()["intervals"]) == 1
    # 迟到读数：00:15 实际正常，原区间被截断为两段，且均不足最短时长
    submit("r5", "2026-01-01T00:15:00Z", 0.0)
    body = anomalies().json()
    assert body["revision"] == 5
    assert body["intervals"] == []


def test_late_reading_extends_interval():
    register()
    submit("r1", "2026-01-01T00:00:00Z", 5.0)
    submit("r2", "2026-01-01T00:20:00Z", 5.0)
    assert anomalies().json()["intervals"] == []  # 间隔 20 分钟 > 上限，两段各自时长为 0
    submit("r3", "2026-01-01T00:10:00Z", 5.0)  # 迟到读数把两段连成一个区间
    body = anomalies().json()
    assert body["intervals"] == [
        {"start": "2026-01-01T00:00:00Z", "end": "2026-01-01T00:20:00Z"}
    ]


def test_window_intersection():
    register()
    submit("r1", "2026-01-01T00:00:00Z", 5.0)
    submit("r2", "2026-01-01T00:10:00Z", 5.0)
    submit("r3", "2026-01-01T00:20:00Z", 5.0)
    # 窗口与区间相交（部分重叠）
    body = anomalies(frm="2026-01-01T00:15:00Z", to="2026-01-01T01:00:00Z").json()
    assert len(body["intervals"]) == 1
    # 窗口不相交：无区间，但仍返回当前修订号
    body = anomalies(frm="2026-01-02T00:00:00Z", to="2026-01-03T00:00:00Z").json()
    assert body["intervals"] == []
    assert body["revision"] == 3


def test_invalid_window_rejected():
    register()
    assert anomalies(frm="2026-01-02T00:00:00Z", to="2026-01-01T00:00:00Z").status_code == 422
    assert anomalies(frm="2026-01-01T00:00:00Z", to="2026-01-01T00:00:00Z").status_code == 422
    assert anomalies(frm="2026-01-01T00:00:00", to="2026-01-02T00:00:00Z").status_code == 422


def test_anomalies_unknown_probe_404():
    assert anomalies(probe_id="ghost").status_code == 404


def test_timezone_offsets_normalized():
    register()
    submit("r1", "2026-01-01T08:00:00+08:00", 5.0)  # 即 2026-01-01T00:00:00Z
    submit("r2", "2026-01-01T00:10:00Z", 5.0)
    submit("r3", "2026-01-01T00:20:00Z", 5.0)
    body = anomalies().json()
    assert body["intervals"] == [
        {"start": "2026-01-01T00:00:00Z", "end": "2026-01-01T00:20:00Z"}
    ]
