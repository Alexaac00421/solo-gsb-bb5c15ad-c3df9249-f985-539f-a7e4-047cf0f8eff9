import os

# 默认用内存 SQLite；设置 DATABASE_URL 可对真实 PostgreSQL 跑同一套测试
os.environ.setdefault("DATABASE_URL", "sqlite://")
