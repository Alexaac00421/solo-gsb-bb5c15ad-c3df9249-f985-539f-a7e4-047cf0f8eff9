#!/usr/bin/env bash
# 调用示例：登记探头 → 上报读数 → 幂等/冲突 → 迟到修正 → 查询异常区间
# 用法: BASE=http://localhost:8000 ./examples/demo.sh
set -euo pipefail
BASE=${BASE:-http://localhost:8000}
PROBE=${PROBE:-freezer-a-1}

step() { printf '\n=== %s ===\n' "$1"; }

step "1. 登记探头（上下限 -25~-18℃，最大采样间隔 600s，最短异常时长 900s）"
curl -sS -X POST "$BASE/probes" -H 'Content-Type: application/json' -d "{
  \"probe_id\": \"$PROBE\",
  \"lower_limit\": -25.0,
  \"upper_limit\": -18.0,
  \"max_interval_seconds\": 600,
  \"min_duration_seconds\": 900
}" -w '\nHTTP %{http_code}\n'

step "2. 上报读数：00:05~00:20 越界（-10℃），其余正常"
E0=$(date -u -d "2026-10-01T00:00:00Z" +%s 2>/dev/null || date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "2026-10-01T00:00:00Z" +%s)
ts_of() { # 分钟偏移 → ISO 时间戳（兼容 GNU/BSD date）
  if date --version >/dev/null 2>&1; then
    date -u -d "@$((E0 + $1 * 60))" +%Y-%m-%dT%H:%M:%SZ
  else
    date -u -r "$((E0 + $1 * 60))" +%Y-%m-%dT%H:%M:%SZ
  fi
}
for m in 0 5 10 15 20 25 30; do
  if [ "$m" -ge 5 ] && [ "$m" -le 20 ]; then T=-10.0; else T=-20.0; fi
  TS=$(ts_of "$m")
  curl -sS -X POST "$BASE/probes/$PROBE/readings" -H 'Content-Type: application/json' \
    -d "{\"source_reading_id\": \"r$m\", \"sampled_at\": \"$TS\", \"temperature\": $T}" \
    -w ' <- HTTP %{http_code}\n'
done

step "3. 幂等重传 r5（同编号同内容）→ 200 duplicate，修订号不变"
curl -sS -X POST "$BASE/probes/$PROBE/readings" -H 'Content-Type: application/json' \
  -d '{"source_reading_id": "r5", "sampled_at": "2026-10-01T00:05:00Z", "temperature": -10.0}' \
  -w '\nHTTP %{http_code}\n'

step "4. 冲突示例：同编号不同内容 → 409；同采样时间不同编号 → 409"
curl -sS -X POST "$BASE/probes/$PROBE/readings" -H 'Content-Type: application/json' \
  -d '{"source_reading_id": "r5", "sampled_at": "2026-10-01T00:05:00Z", "temperature": -11.0}' \
  -w '\nHTTP %{http_code}\n'
curl -sS -X POST "$BASE/probes/$PROBE/readings" -H 'Content-Type: application/json' \
  -d '{"source_reading_id": "rX", "sampled_at": "2026-10-01T00:05:00Z", "temperature": -10.0}' \
  -w '\nHTTP %{http_code}\n'

step "5. 查询异常区间（应含 00:05~00:20 一段，时长 900s）"
curl -sS "$BASE/probes/$PROBE/anomalies?from=2026-10-01T00:00:00Z&to=2026-10-01T01:00:00Z" \
  -w '\nHTTP %{http_code}\n'

step "6. 迟到读数：00:12 补一条正常值 -20℃ → 区间被截断为两段且均不达标 → 异常消失"
curl -sS -X POST "$BASE/probes/$PROBE/readings" -H 'Content-Type: application/json' \
  -d '{"source_reading_id": "late1", "sampled_at": "2026-10-01T00:12:00Z", "temperature": -20.0}' \
  -w '\nHTTP %{http_code}\n'
curl -sS "$BASE/probes/$PROBE/anomalies?from=2026-10-01T00:00:00Z&to=2026-10-01T01:00:00Z" \
  -w '\nHTTP %{http_code}\n'

step "7. 未登记探头 → 404；无效参数 → 422"
curl -sS -X POST "$BASE/probes/ghost/readings" -H 'Content-Type: application/json' \
  -d '{"source_reading_id": "g1", "sampled_at": "2026-10-01T00:00:00Z", "temperature": -20.0}' \
  -w '\nHTTP %{http_code}\n'
curl -sS -X POST "$BASE/probes/$PROBE/readings" -H 'Content-Type: application/json' \
  -d '{"source_reading_id": "bad1", "sampled_at": "2026-10-01T00:00:00", "temperature": -20.0}' \
  -w '\nHTTP %{http_code}\n'
