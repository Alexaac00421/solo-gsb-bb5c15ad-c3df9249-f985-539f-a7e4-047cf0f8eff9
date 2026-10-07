"""数据库引擎与会话管理。"""
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from . import config

engine = create_engine(config.DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    """FastAPI 依赖：每个请求一个会话。"""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
