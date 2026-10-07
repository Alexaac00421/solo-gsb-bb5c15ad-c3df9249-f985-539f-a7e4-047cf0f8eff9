#!/usr/bin/env bash
# 调用示例：先启动服务（docker compose up --build），再执行本脚本
set -euo pipefail

BASE="${BASE_URL:-http://localhost:8000}"

echo "== 1. 登记探头：温度上下限 [-2, 2]℃，最大采样间隔 600s，最短异常时长 1200s =="
curl -sS -X POST "$BASE/probes" -H 'Content-Type: application/json' -d '{
  "probe_id": "cold-room-1",
  "min_temp": -2.0,
  "max_temp": 2.0,
  "max_interval_seconds": 600,
  "min_duration_seconds": 1200
}'
echo; echo

echo "== 2. 上报越界读数（08:00 +08:00 即 00:00Z）=="
curl -sS -X POST "$BASE/probes/cold-room-1/readings" -H 'Content-Type: application/json' -d '{
  "reading_id": "r1", "sampled_at": "2026-01-01T08:00:00+08:00", "temperature": 5.2
}'
echo; echo

echo "== 3. 继续上报越界读数 =="
curl -sS -X POST "$BASE/probes/cold-room-1/readings" -H 'Content-Type: application/json' -d '{
  "reading_id": "r2", "sampled_at": "2026-01-01T00:10:00Z", "temperature": 6.1
}'
echo
curl -sS -X POST "$BASE/probes/cold-room-1/readings" -H 'Content-Type: application/json' -d '{
  "reading_id": "r3", "sampled_at": "2026-01-01T00:20:00Z", "temperature": 5.5
}'
echo; echo

echo "== 4. 同探头同编号同内容重传：幂等接受，duplicate=true，修订号不变 =="
curl -sS -X POST "$BASE/probes/cold-room-1/readings" -H 'Content-Type: application/json' -d '{
  "reading_id": "r3", "sampled_at": "2026-01-01T00:20:00Z", "temperature": 5.5
}'
echo; echo

echo "== 5. 同编号不同内容：409 冲突 =="
curl -sS -o /dev/stderr -w 'HTTP %{http_code}\n\n' -X POST "$BASE/probes/cold-room-1/readings" \
  -H 'Content-Type: application/json' -d '{
  "reading_id": "r3", "sampled_at": "2026-01-01T00:20:00Z", "temperature": 9.9
}' || true

echo "== 6. 同采样时间不同编号：409 冲突 =="
curl -sS -o /dev/stderr -w 'HTTP %{http_code}\n\n' -X POST "$BASE/probes/cold-room-1/readings" \
  -H 'Content-Type: application/json' -d '{
  "reading_id": "rX", "sampled_at": "2026-01-01T00:20:00Z", "temperature": 5.5
}' || true

echo "== 7. 未登记探头：404 =="
curl -sS -o /dev/stderr -w 'HTTP %{http_code}\n\n' -X POST "$BASE/probes/ghost/readings" \
  -H 'Content-Type: application/json' -d '{
  "reading_id": "r1", "sampled_at": "2026-01-01T00:00:00Z", "temperature": 5.0
}' || true

echo "== 8. 查询异常区间（与时间窗相交）及修订号 =="
curl -sS "$BASE/probes/cold-room-1/anomalies?from=2026-01-01T00:00:00Z&to=2026-01-02T00:00:00Z"
echo; echo

echo "== 9. 迟到读数修正区间：补一条 00:15 的正常读数，原区间被截断 =="
curl -sS -X POST "$BASE/probes/cold-room-1/readings" -H 'Content-Type: application/json' -d '{
  "reading_id": "r4-late", "sampled_at": "2026-01-01T00:15:00Z", "temperature": 0.5
}'
echo
curl -sS "$BASE/probes/cold-room-1/anomalies?from=2026-01-01T00:00:00Z&to=2026-01-02T00:00:00Z"
echo
