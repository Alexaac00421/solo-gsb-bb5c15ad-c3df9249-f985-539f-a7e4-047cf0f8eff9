"""端到端集成测试：真实 PostgreSQL（通过环境变量 DATABASE_URL 提供）。

运行方式：
    DATABASE_URL=postgresql+psycopg2://... pytest tests/test_api.py
未设置 DATABASE_URL 时自动跳过。
"""
import os
import uuid

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="需要 DATABASE_URL 指向 PostgreSQL"
)

from app.main import app  # noqa: E402

BASE = "2026-10-01T00:00:00+00:00"
PROBE_BODY = {
    "lower_limit": -25.0,
    "upper_limit": -18.0,
    "max_interval_seconds": 600,
    "min_duration_seconds": 900,
}


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def probe_id(client):
    pid = f"probe-{uuid.uuid4().hex[:12]}"
    r = client.post("/probes", json={"probe_id": pid, **PROBE_BODY})
    assert r.status_code == 201, r.text
    return pid


def ts(minutes: int) -> str:
    from datetime import datetime, timedelta, timezone

    t = datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes)
    return t.isoformat()


def parse_ts(s: str):
    from datetime import datetime

    return datetime.fromisoformat(s)


def reading(rid: str, minutes: int, temp: float) -> dict:
    return {"source_reading_id": rid, "sampled_at": ts(minutes), "temperature": temp}


def test_register_probe_validation(client):
    pid = f"reg-{uuid.uuid4().hex[:12]}"
    # 间隔/时长必须为正数
    r = client.post("/probes", json={"probe_id": pid, **PROBE_BODY, "max_interval_seconds": 0})
    assert r.status_code == 422
    r = client.post("/probes", json={"probe_id": pid, **PROBE_BODY, "min_duration_seconds": -5})
    assert r.status_code == 422
    # 下限必须小于上限
    r = client.post("/probes", json={"probe_id": pid, **PROBE_BODY, "lower_limit": -10.0})
    assert r.status_code == 422
    # 正常登记
    r = client.post("/probes", json={"probe_id": pid, **PROBE_BODY})
    assert r.status_code == 201
    assert r.json()["revision"] == 0
    # 同配置重复登记 → 幂等 200
    r = client.post("/probes", json={"probe_id": pid, **PROBE_BODY})
    assert r.status_code == 200
    # 不同配置 → 409
    r = client.post("/probes", json={"probe_id": pid, **PROBE_BODY, "upper_limit": -15.0})
    assert r.status_code == 409


def test_reading_unregistered_probe(client):
    r = client.post("/probes/no-such-probe/readings", json=reading("r1", 0, -20.0))
    assert r.status_code == 404


def test_reading_invalid_params(client, probe_id):
    # 无时区时间
    r = client.post(f"/probes/{probe_id}/readings",
                    json={"source_reading_id": "r1", "sampled_at": "2026-10-01T00:00:00",
                          "temperature": -20.0})
    assert r.status_code == 422
    # 非法温度
    r = client.post(f"/probes/{probe_id}/readings",
                    json={"source_reading_id": "r1", "sampled_at": ts(0),
                          "temperature": "hot"})
    assert r.status_code == 422
    # 空编号
    r = client.post(f"/probes/{probe_id}/readings",
                    json={"source_reading_id": "", "sampled_at": ts(0),
                          "temperature": -20.0})
    assert r.status_code == 422


def test_idempotent_replay_and_conflicts(client, probe_id):
    r = client.post(f"/probes/{probe_id}/readings", json=reading("r1", 0, -20.0))
    assert r.status_code == 201
    assert r.json()["status"] == "accepted"
    assert r.json()["revision"] == 1

    # 同编号同内容重传 → 幂等，修订号不变
    r = client.post(f"/probes/{probe_id}/readings", json=reading("r1", 0, -20.0))
    assert r.status_code == 200
    assert r.json()["status"] == "duplicate"
    assert r.json()["revision"] == 1

    # 同编号不同内容 → 409
    r = client.post(f"/probes/{probe_id}/readings", json=reading("r1", 0, -21.0))
    assert r.status_code == 409
    r = client.post(f"/probes/{probe_id}/readings", json=reading("r1", 5, -20.0))
    assert r.status_code == 409

    # 同采样时间不同编号 → 409
    r = client.post(f"/probes/{probe_id}/readings", json=reading("r2", 0, -20.0))
    assert r.status_code == 409

    # 冲突与重传均未递增修订号
    r = client.get(f"/probes/{probe_id}")
    assert r.json()["revision"] == 1


def test_interval_formation_and_query(client, probe_id):
    # 越界序列 5..20 分钟，时长 15 分钟，达标
    for i, m in enumerate([5, 10, 15, 20]):
        r = client.post(f"/probes/{probe_id}/readings", json=reading(f"r{i}", m, -10.0))
        assert r.status_code == 201
    # 正常读数与无效短序列
    client.post(f"/probes/{probe_id}/readings", json=reading("n1", 25, -20.0))
    client.post(f"/probes/{probe_id}/readings", json=reading("s1", 30, -10.0))

    r = client.get(f"/probes/{probe_id}/anomalies",
                   params={"from": ts(0), "to": ts(60)})
    assert r.status_code == 200
    body = r.json()
    assert body["revision"] == 6
    assert len(body["intervals"]) == 1
    iv = body["intervals"][0]
    assert parse_ts(iv["start"]) == parse_ts(ts(5))
    assert parse_ts(iv["end"]) == parse_ts(ts(20))
    assert iv["duration_seconds"] == 900.0

    # 不相交的时间窗 → 空
    r = client.get(f"/probes/{probe_id}/anomalies",
                   params={"from": ts(100), "to": ts(200)})
    assert r.json()["intervals"] == []
    # 部分相交 → 命中
    r = client.get(f"/probes/{probe_id}/anomalies",
                   params={"from": ts(18), "to": ts(100)})
    assert len(r.json()["intervals"]) == 1


def test_query_window_validation(client, probe_id):
    r = client.get(f"/probes/{probe_id}/anomalies",
                   params={"from": "2026-10-01T00:00:00", "to": ts(10)})
    assert r.status_code == 422
    r = client.get(f"/probes/{probe_id}/anomalies",
                   params={"from": ts(10), "to": ts(0)})
    assert r.status_code == 422
    r = client.get("/probes/ghost/anomalies", params={"from": ts(0), "to": ts(10)})
    assert r.status_code == 404


def test_late_reading_corrects_intervals(client, probe_id):
    # 两段越界序列被 15 分钟缺口分开，均不达标
    for i, m in enumerate([0, 5, 20, 25]):
        client.post(f"/probes/{probe_id}/readings", json=reading(f"a{i}", m, -10.0))
    r = client.get(f"/probes/{probe_id}/anomalies", params={"from": ts(0), "to": ts(60)})
    assert r.json()["intervals"] == []

    # 迟到读数填平缺口 → 合并为一段 25 分钟异常
    client.post(f"/probes/{probe_id}/readings", json=reading("late1", 10, -10.0))
    client.post(f"/probes/{probe_id}/readings", json=reading("late2", 15, -10.0))
    r = client.get(f"/probes/{probe_id}/anomalies", params={"from": ts(0), "to": ts(60)})
    body = r.json()
    assert body["revision"] == 6
    assert len(body["intervals"]) == 1
    assert parse_ts(body["intervals"][0]["start"]) == parse_ts(ts(0))
    assert parse_ts(body["intervals"][0]["end"]) == parse_ts(ts(25))

    # 迟到的正常读数将区间截断为两段（各 10 分钟，均不达标）→ 异常消失
    client.post(f"/probes/{probe_id}/readings", json=reading("late3", 12, -20.0))
    r = client.get(f"/probes/{probe_id}/anomalies", params={"from": ts(0), "to": ts(60)})
    assert r.json()["revision"] == 7
    assert r.json()["intervals"] == []
