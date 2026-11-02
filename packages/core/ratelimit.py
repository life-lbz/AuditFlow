"""固定窗口限流：优先 Redis（多副本共享），不可用时降级为进程内计数。

Redis 使用的 key 形如 ``rl:{scope}:{window_start}``，单条 pipeline 完成 INCR + EXPIRE，
多副本共享同一配额；进程内模式只适用于单副本或测试。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from .redis_client import get_redis


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_after: int


class _MemoryCounter:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._windows: dict[str, tuple[int, int]] = {}

    async def hit(self, key: str, limit: int, window: int) -> RateLimitResult:
        window_id = int(time.time()) // window
        async with self._lock:
            stored_key, count = self._windows.get(key, (window_id, 0))
            if stored_key != window_id:
                count = 0
            count += 1
            self._windows[key] = (window_id, count)
            if len(self._windows) > 4096:  # 防止长期运行内存膨胀
                self._windows = {
                    k: v for k, v in self._windows.items() if v[0] == window_id
                }
        remaining = max(limit - count, 0)
        reset_after = window - int(time.time()) % window
        return RateLimitResult(count <= limit, limit, remaining, reset_after)


class RateLimiter:
    def __init__(self) -> None:
        self._memory = _MemoryCounter()

    async def hit(self, scope: str, key: str, limit: int, window: int = 60) -> RateLimitResult:
        client = await get_redis()
        if client is None:
            return await self._memory.hit(f"{scope}:{key}", limit, window)

        window_id = int(time.time()) // window
        redis_key = f"rl:{scope}:{key}:{window_id}"
        try:
            pipe = client.pipeline()
            pipe.incr(redis_key)
            pipe.expire(redis_key, window + 5)
            count, _ = await pipe.execute()
        except Exception:  # noqa: BLE001 - Redis 抖动时降级为内存计数
            return await self._memory.hit(f"{scope}:{key}", limit, window)

        count = int(count)
        remaining = max(limit - count, 0)
        reset_after = window - int(time.time()) % window
        return RateLimitResult(count <= limit, limit, remaining, reset_after)


limiter = RateLimiter()
