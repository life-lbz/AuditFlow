"""共享 Redis 异步客户端（限流与案件锁使用）。

设计原则：Redis 只是增强能力，不是可用性前提。连接失败时在冷却期内直接返回 None，
调用方自动降级到进程内实现，避免每次请求都等待连接超时。
"""

from __future__ import annotations

import time

from .config import get_settings

_cooldown_until = 0.0
_healthy_until = 0.0
_client = None


def _wanted() -> bool:
    settings = get_settings()
    return settings.rate_limit_backend == "redis" or settings.case_lock_backend == "redis"


async def get_redis():
    """返回可用的 Redis 客户端；未启用、不可达或冷却期内返回 None。"""
    global _cooldown_until, _healthy_until, _client

    if not _wanted():
        return None
    now = time.monotonic()
    if now < _cooldown_until:
        return None
    if _client is not None and now < _healthy_until:
        return _client

    try:
        from redis.asyncio import Redis

        settings = get_settings()
        if _client is None:
            _client = Redis.from_url(
                settings.redis_url,
                socket_connect_timeout=settings.redis_timeout_secs,
                socket_timeout=settings.redis_timeout_secs,
                health_check_interval=30,
                decode_responses=True,
            )
        await _client.ping()
    except Exception:  # noqa: BLE001 - 任何 Redis 异常都不应影响主流程
        _cooldown_until = now + 30.0
        return None

    _healthy_until = now + 30.0
    return _client


async def close_redis() -> None:
    global _client, _healthy_until
    if _client is None:
        return
    try:
        await _client.aclose()
    except Exception:  # noqa: BLE001
        pass
    _client = None
    _healthy_until = 0.0
