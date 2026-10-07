"""应用配置：全部可通过环境变量覆盖。"""
import os

# SQLAlchemy 数据库连接串，默认指向 compose 中的 postgres 服务
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://coldchain:coldchain@localhost:5432/coldchain",
)

# 启动时等待数据库就绪的重试次数与间隔（秒）
DB_CONNECT_RETRIES = int(os.getenv("DB_CONNECT_RETRIES", "30"))
DB_CONNECT_RETRY_INTERVAL = float(os.getenv("DB_CONNECT_RETRY_INTERVAL", "1.0"))
