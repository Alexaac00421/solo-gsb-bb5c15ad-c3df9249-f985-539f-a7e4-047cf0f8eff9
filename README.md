# 冷库温度异常区间服务

基于 **Python / FastAPI + PostgreSQL** 的冷库温度记录仪异常区间服务。

- 登记探头时固定：温度上下限、正数最大采样间隔、正数最短异常时长；
- 读数摄入支持幂等重传与冲突拒绝，迟到读数自动修正受影响区间；
- 每条新接受读数使探头修订号 +1，查询返回与时间窗相交的当前异常区间及修订号。

## 一键启动（Docker Compose）

```bash
docker compose up --build
```

- API 地址：**http://localhost:8000**（交互式文档 http://localhost:8000/docs）
- PostgreSQL：**localhost:5432**（库 `coldchain`，账号/口令默认 `postgres`/`postgres`）
- 数据库初始化（建表）由 API 容器启动时自动完成，无需手工执行 SQL。

## 配置

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg2://postgres:postgres@db:5432/coldchain`（compose 内） | SQLAlchemy 连接串 |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `postgres` / `postgres` / `coldchain` | Compose 中数据库初始化参数 |

端口：`8000`（API）、`5432`（PostgreSQL，仅需本机访问时可删除 compose 中的端口映射）。

## API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/probes` | 登记探头（同配置重复登记幂等 200；配置冲突 409） |
| `GET` | `/probes/{probe_id}` | 查询探头配置与当前修订号 |
| `POST` | `/probes/{probe_id}/readings` | 上报读数（201 接受 / 200 幂等重传 / 404 未登记 / 409 冲突 / 422 参数无效） |
| `GET` | `/probes/{probe_id}/anomalies?from=...&to=...` | 查询与时间窗相交的当前异常区间及修订号 |

### 登记探头

```bash
curl -X POST http://localhost:8000/probes -H 'Content-Type: application/json' -d '{
  "probe_id": "freezer-a-1",
  "lower_limit": -25.0,
  "upper_limit": -18.0,
  "max_interval_seconds": 600,
  "min_duration_seconds": 900
}'
# 201 {"probe_id":"freezer-a-1",...,"revision":0}
```

`max_interval_seconds`、`min_duration_seconds` 必须为正数，`lower_limit < upper_limit`，否则 422。

### 上报读数

```bash
curl -X POST http://localhost:8000/probes/freezer-a-1/readings \
  -H 'Content-Type: application/json' -d '{
    "source_reading_id": "r0001",
    "sampled_at": "2026-10-01T00:05:00+08:00",
    "temperature": -10.5
  }'
# 201 {"status":"accepted","revision":1}
```

- `sampled_at` 必须带时区；同一时刻的不同时区写法（如 `+08:00` 与 `Z`）视为同一内容。
- **幂等**：同探头同 `source_reading_id` 且内容一致的重传返回 `200 {"status":"duplicate"}`，修订号不变。
- **冲突（409）**：同编号但内容不一致；或同探头同采样时间但编号不同。
- **404**：探头未登记；**422**：参数无效（空编号、无时区时间、非数值温度等）。

### 查询异常区间

```bash
curl 'http://localhost:8000/probes/freezer-a-1/anomalies?from=2026-10-01T00:00:00Z&to=2026-10-02T00:00:00Z'
# {"probe_id":"freezer-a-1","revision":12,"window_from":...,"window_to":...,
#  "intervals":[{"start":"2026-10-01T00:05:00Z","end":"2026-10-01T00:20:00Z","duration_seconds":900.0}]}
```

返回与 `[from, to]`（闭区间，均须带时区）相交的**当前**异常区间；区间不裁剪到窗口。

## 区间判定规则

- 温度**严格**低于下限或**严格**高于上限为越界（恰等于限值视为正常）；
- 按采样时间升序，连续越界读数构成候选区间，起止取首、末越界读数的采样时间；
- 相邻读数间隔 **>** `max_interval_seconds` 时截断（等于不算超）；正常读数同样截断；
- 区间时长（末 − 首）**≥** `min_duration_seconds` 才输出为异常；
- 迟到读数参与全量重算，受影响区间会被合并、拆分、延长或消除。

## 一致性与并发

- 摄入时对探头行 `SELECT ... FOR UPDATE`，同一探头的并发写入被串行化；
- 接受新读数 → 修订号 +1 → 重算并整体替换区间，在**同一事务**提交；
- 查询用单条 SQL 取 `revision` 与区间（单语句快照），因此不会出现与修订号不一致的区间；
- 重传、冲突、拒绝均不改变修订号。

## 本地开发与测试

```bash
pip install -r requirements.txt
pip install pytest httpx

# 单元测试（区间算法，无需数据库）
pytest tests/test_intervals.py

# 集成测试（需要 PostgreSQL）
export DATABASE_URL='postgresql+psycopg2://postgres:postgres@localhost:5432/coldchain'
uvicorn app.main:app --port 8000 &
pytest tests/test_api.py

# 并发一致性校验（需服务已启动）
python tests/concurrency_check.py

# 调用示例脚本
BASE=http://localhost:8000 ./examples/demo.sh
```

## 目录结构

```
app/
  main.py       # FastAPI 入口与路由
  service.py    # 摄入/幂等/冲突/重算/查询核心逻辑
  intervals.py  # 区间计算纯函数
  models.py     # ORM：probes / readings / anomaly_intervals
  schemas.py    # 请求响应模型与校验
  db.py         # 引擎与会话
tests/          # 单元 + 集成 + 并发校验
examples/demo.sh
Dockerfile / docker-compose.yml
```

> 说明：当前实现每次接受读数后按该探头全部读数重算区间（O(n)），语义最简单且严格正确；
> 数据量大时可在 `service.ingest_reading` 中替换为增量维护，接口语义不变。
