"""数据库会话与引擎（SQLAlchemy 2.0 异步）。

生产环境启用连接池参数与连接前探活（pool_pre_ping），避免长连接被中间设备断开后
第一个请求必然失败；池大小可通过 DB_POOL_SIZE / DB_MAX_OVERFLOW 调整。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from .config import get_settings
from .models import Base

DEFAULT_URL = "postgresql+asyncpg://auditflow:auditflow@localhost:5433/auditflow"


def get_database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


def create_engine(url: str | None = None):
    target = url or get_database_url()
    settings = get_settings()
    kwargs: dict = {"echo": False}
    if target.startswith("sqlite"):
        # 内存 SQLite 需要共享连接池，否则每个连接看到的是不同数据库
        kwargs["poolclass"] = StaticPool
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs.update(
            pool_pre_ping=True,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_recycle=settings.db_pool_recycle_secs,
            pool_timeout=settings.db_pool_timeout_secs,
            connect_args={"timeout": settings.db_connect_timeout_secs},
        )
    return create_async_engine(target, **kwargs)


engine = create_engine()
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """开发环境建表（生产环境走 Alembic 迁移）。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
