"""并发写入一致性验证：多线程并发提交新读数与重传，验证修订号与区间一致。"""
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx

BASE = os.getenv("BASE", "http://127.0.0.1:8000")
T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def main() -> int:
    probe_id = f"conc-{uuid.uuid4().hex[:8]}"
    c = httpx.Client(base_url=BASE, timeout=30)

    r = c.post("/probes", json={
        "probe_id": probe_id,
        "lower_limit": -25.0, "upper_limit": -18.0,
        "max_interval_seconds": 600, "min_duration_seconds": 900,
    })
    assert r.status_code == 201, r.text

    # 40 条越界读数（每分钟一条，温度 -10），由 8 个线程并发提交；
    # 其中一半请求发送两次（重传），检验幂等。
    def send(i: int):
        body = {
            "source_reading_id": f"r{i:03d}",
            "sampled_at": (T0 + timedelta(minutes=i)).isoformat(),
            "temperature": -10.0,
        }
        results = []
        for _ in range(2 if i % 2 == 0 else 1):
            resp = c.post(f"/probes/{probe_id}/readings", json=body)
            results.append(resp)
        return results

    accepted, duplicates, errors = 0, 0, []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for results in ex.map(send, range(40)):
            for resp in results:
                if resp.status_code == 201:
                    accepted += 1
                elif resp.status_code == 200:
                    duplicates += 1
                else:
                    errors.append((resp.status_code, resp.text))

    assert not errors, f"出现非预期错误: {errors[:3]}"
    assert accepted == 40, f"accepted={accepted}"
    assert duplicates == 20, f"duplicates={duplicates}"

    # 查询验证：修订号必须等于 40，区间必须与本地重算一致
    r = c.get(f"/probes/{probe_id}/anomalies",
              params={"from": T0.isoformat(),
                      "to": (T0 + timedelta(minutes=60)).isoformat()})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["revision"] == 40, f"revision={body['revision']}"
    ivs = body["intervals"]
    assert len(ivs) == 1, f"intervals={ivs}"
    assert ivs[0]["start"].replace("Z", "+00:00") == T0.isoformat()
    assert ivs[0]["duration_seconds"] == 39 * 60

    # 再查一次探头详情，修订号一致
    r = c.get(f"/probes/{probe_id}")
    assert r.json()["revision"] == 40

    print(f"OK probe={probe_id} accepted={accepted} duplicates={duplicates} "
          f"revision={body['revision']} intervals={len(ivs)} "
          f"[{ivs[0]['start']} .. {ivs[0]['end']}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
