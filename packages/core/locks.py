"""案件级互斥锁：防止同一案件被并发 run / retry / approve。

memory 后端适用于单副本；redis 后端通过 SET NX PX + Lua 比对 token 释放，
多副本部署时保证同一案件同一时刻只有一个编排在跑。
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager

from .config import get_settings
from .redis_client import get_redis

_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class LockBusy(Exception):
    """锁已被其他请求持有。"""


class _MemoryLocks:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def acquire(self, key: str) -> None:
        async with self._guard:
            lock = self._locks.setdefault(key, asyncio.Lock())
        if lock.locked():
            raise LockBusy(key)
        await lock.acquire()

    async def release(self, key: str) -> None:
        async with self._guard:
            lock = self._locks.get(key)
        if lock and lock.locked():
            lock.release()


_memory = _MemoryLocks()


@asynccontextmanager
async def case_lock(case_id: str, *, ttl_seconds: int | None = None):
    """获取案件锁；失败抛 LockBusy。上下文退出时自动释放。"""
    settings = get_settings()
    ttl = int(ttl_seconds or settings.case_timeout_secs)
    key = f"lock:case:{case_id}"
    token = uuid.uuid4().hex
    client = await get_redis() if settings.case_lock_backend == "redis" else None

    if client is not None:
        try:
            acquired = await client.set(key, token, nx=True, px=ttl * 1000)
        except Exception:  # noqa: BLE001 - Redis 异常时退回内存锁
            acquired = None
        if acquired:
            try:
                yield
            finally:
                try:
                    await client.eval(_RELEASE_SCRIPT, 1, key, token)
                except Exception:  # noqa: BLE001
                    pass
            return
        if acquired is False:
            raise LockBusy(case_id)

    # memory 后端或 Redis 不可用
    await _memory.acquire(key)
    try:
        yield
    finally:
        await _memory.release(key)
