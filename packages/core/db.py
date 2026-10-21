"""数据库会话与引擎（SQLAlchemy 2.0 异步）。"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from .models import Base

DEFAULT_URL = "postgresql+asyncpg://auditflow:auditflow@localhost:5433/auditflow"


def get_database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


def create_engine(url: str | None = None):
    target = url or get_database_url()
    kwargs: dict = {"echo": False}
    if target.startswith("sqlite"):
        # 内存 SQLite 需要共享连接池，否则每个连接看到的是不同数据库
        kwargs["poolclass"] = StaticPool
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_pre_ping"] = True
    return create_async_engine(target, **kwargs)


engine = create_engine()
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """开发环境建表（生产环境走 Alembic 迁移）。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
