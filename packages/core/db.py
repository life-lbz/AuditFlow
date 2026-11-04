"""数据库会话与引擎（SQLAlchemy 2.0 异步）。

生产环境启用连接池参数与连接前探活（pool_pre_ping），避免长连接被中间设备断开后
第一个请求必然失败；池大小可通过 DB_POOL_SIZE / DB_MAX_OVERFLOW 调整。

engine 与 SessionLocal 在首次使用时才创建，避免 Alembic 执行期间 import 本模块
拿到过期 DATABASE_URL（Alembic env.py 自己 build engine）。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from .config import get_settings
from .models import Base

DEFAULT_URL = "postgresql+asyncpg://auditflow:auditflow@localhost:5433/auditflow"


def get_database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


def _build_engine(url: str | None = None) -> AsyncEngine:
    target = url or get_database_url()
    settings = get_settings()
    kwargs: dict = {"echo": False}
    if target.startswith("sqlite"):
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


# ---- lazy module-level state ----
_engine: AsyncEngine | None = None
_session_local: async_sessionmaker[AsyncSession] | None = None


def _ensure_engine() -> AsyncEngine:
    global _engine, _session_local
    if _engine is None:
        _engine = _build_engine()
        _session_local = async_sessionmaker(
            _engine, class_=AsyncSession, expire_on_commit=False
        )
    return _engine


async def close_engine() -> None:
    """关闭引擎并释放连接池（应用优雅关闭时调用）。"""
    global _engine, _session_local
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_local = None


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：每次请求独立会话，异常自动回滚。"""
    maker = _ensure_engine()  # noqa: F841 - 触发初始化，真正用的是模块级 _session_local
    # maker 变量只是为了触发上面的 assert
    assert _session_local is not None
    async with _session_local() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """开发环境建表（生产环境走 Alembic 迁移）。"""
    engine = _ensure_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# 向后兼容：允许 `from packages.core.db import engine, SessionLocal`
def __getattr__(name: str):  # noqa: D401 - 模块级钩子
    if name == "engine":
        return _ensure_engine()
    if name == "SessionLocal":
        _ensure_engine()
        return _session_local
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
