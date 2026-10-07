# 冷库温度异常区间服务

基于 **Python / FastAPI / PostgreSQL** 的冷库温度记录仪异常区间服务：登记探头阈值配置，接收温度读数（幂等、防冲突），按规则实时计算并物化异常区间，支持迟到读数修正与并发一致的修订号查询。

## 一键启动

```bash
docker compose up --build
```

- 启动后自动完成数据库初始化（应用启动时建表，并重试等待数据库就绪）。
- API 地址：**http://localhost:8000**
- 交互式文档（Swagger UI）：**http://localhost:8000/docs**
- PostgreSQL：**localhost:5432**（用户/密码/库名均为 `coldchain`，数据持久化在 `dbdata` 卷）

运行调用示例：

```bash
./examples/demo.sh
```

## 配置

通过环境变量配置（`docker-compose.yml` 中已为 `app` 服务设置好）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg2://coldchain:coldchain@localhost:5432/coldchain` | SQLAlchemy 连接串 |
| `DB_CONNECT_RETRIES` | `30` | 启动时等待数据库就绪的重试次数 |
| `DB_CONNECT_RETRY_INTERVAL` | `1.0` | 重试间隔（秒） |

## 业务规则

- **登记探头**：固定温度上下限（`min_temp < max_temp`）、正数最大采样间隔 `max_interval_seconds`、正数最短异常时长 `min_duration_seconds`；重复登记返回 409。
- **读数**：探头编号 + 来源读数编号 + 带时区采样时间 + 温度。
  - 同探头、同编号、同内容重传：幂等接受（`duplicate=true`），**修订号不递增**；
  - 同编号但内容不同：**409 拒绝**；同探头同采样时间但编号不同：**409 拒绝**；
  - 未登记探头：404；无效参数（非正数间隔/时长、上下限颠倒、无时区时间等）：422。
- **区间计算**：读数按采样时间排序；温度低于下限或高于上限为越界；连续越界读数且相邻间隔不超过 `max_interval_seconds` 形成区间，起止取首、末越界读数的采样时间；正常读数或超长间隔截断；区间时长达到 `min_duration_seconds` 才输出。
- **迟到读数**：每次接受新读数后对该探头全量重算区间，迟到读数自然修正受影响区间。
- **修订号**：每接受一条新读数递增 1；重传不递增；冲突拒绝不递增。
- **并发一致性**：写事务对探头行 `SELECT ... FOR UPDATE` 串行化同一探头的写入；修订号与异常区间在同一事务提交；查询用单条 JOIN 同时取修订号与对应区间，**不会出现区间与修订号不一致的快照**。
- **查询**：返回与有效时间窗 `[from, to]` 相交的当前异常区间及修订号（`from < to` 且均须带时区）。

## API

### `POST /probes` — 登记探头

```bash
curl -X POST http://localhost:8000/probes -H 'Content-Type: application/json' -d '{
  "probe_id": "cold-room-1",
  "min_temp": -2.0,
  "max_temp": 2.0,
  "max_interval_seconds": 600,
  "min_duration_seconds": 1200
}'
```

响应 `201`：

```json
{"probe_id":"cold-room-1","min_temp":-2.0,"max_temp":2.0,"max_interval_seconds":600.0,"min_duration_seconds":1200.0,"revision":0}
```

### `POST /probes/{probe_id}/readings` — 上报读数

```bash
curl -X POST http://localhost:8000/probes/cold-room-1/readings -H 'Content-Type: application/json' -d '{
  "reading_id": "r1",
  "sampled_at": "2026-01-01T08:00:00+08:00",
  "temperature": 5.2
}'
```

响应 `200`（新读数被接受，修订号递增）：

```json
{"probe_id":"cold-room-1","reading_id":"r1","duplicate":false,"revision":1}
```

同内容重传响应（幂等，修订号不变）：

```json
{"probe_id":"cold-room-1","reading_id":"r1","duplicate":true,"revision":1}
```

### `GET /probes/{probe_id}/anomalies?from=...&to=...` — 查询异常区间

```bash
curl 'http://localhost:8000/probes/cold-room-1/anomalies?from=2026-01-01T00:00:00Z&to=2026-01-02T00:00:00Z'
```

响应 `200`：

```json
{
  "probe_id": "cold-room-1",
  "revision": 3,
  "window_from": "2026-01-01T00:00:00Z",
  "window_to": "2026-01-02T00:00:00Z",
  "intervals": [{"start": "2026-01-01T00:00:00Z", "end": "2026-01-01T00:20:00Z"}]
}
```

### 其他

- `GET /probes/{probe_id}` — 查询探头配置与当前修订号
- `GET /health` — 健康检查

### 错误码

| 状态码 | 场景 |
| --- | --- |
| 404 | 探头未登记 |
| 409 | 探头重复登记；同编号读数内容冲突；同采样时间不同编号 |
| 422 | 无效参数（非正数间隔/时长、上下限颠倒、时间缺时区、时间窗 from ≥ to 等） |

## 测试

```bash
pip install -r requirements-dev.txt
pytest tests/                    # 单元 + API 测试（内存 SQLite）
DATABASE_URL=postgresql+psycopg2://coldchain:coldchain@localhost:5432/coldchain pytest tests/
#                                 ^ 对真实 PostgreSQL 运行全部测试，含并发一致性测试
```

`tests/test_concurrency.py` 验证：多线程并发写入后修订号精确等于接受的新读数条数；并发重传只计一次；并发冲突只有一个赢家；写入进行中并发查询不会读到与修订号不一致的区间。

## 项目结构

```
app/
  main.py       # FastAPI 路由、异常映射、启动时建表
  service.py    # 核心事务逻辑（行锁、幂等/冲突判定、重算区间、一致查询）
  intervals.py  # 区间计算纯函数
  models.py     # probes / readings / anomaly_intervals 三张表
  schemas.py    # 请求响应模型与校验
  database.py   # 引擎与会话
  config.py     # 环境变量配置
tests/          # pytest：区间规则、API、并发一致性
examples/demo.sh# curl 调用示例
Dockerfile / docker-compose.yml
```
