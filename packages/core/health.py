"""就绪探针依赖检查（数据库 / 对象存储 / Redis / ERP）。

Liveness 只判断进程是否存活；Readiness 才检查下游依赖，避免下游抖动导致容器被反复重启。
数据库不可用视为 unavailable（503）；其余依赖失败只标记 degraded（仍可返回 200），
因为文档解析、ERP 写入等能力在依赖恢复后会自动恢复，且缓存/降级路径仍可服务。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .redis_client import get_redis
from .storage import get_storage


@dataclass
class CheckResult:
    ok: bool
    detail: str = ""
    latency_ms: float = 0.0


async def _run(name: str, coro, timeout: float) -> tuple[str, CheckResult]:
    started = time.perf_counter()
    try:
        ok, detail = await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        ok, detail = False, f"超时（>{timeout:.1f}s）"
    except Exception as exc:  # noqa: BLE001 - 探针必须自己兜住所有异常
        ok, detail = False, str(exc)[:200]
    return name, CheckResult(ok, detail, round((time.perf_counter() - started) * 1000, 1))


async def _database(session: AsyncSession) -> tuple[bool, str]:
    await session.execute(text("SELECT 1"))
    return True, "connected"


async def _storage() -> tuple[bool, str]:
    await get_storage().ensure_ready()
    return True, f"backend={get_settings().storage_backend}"


async def _redis() -> tuple[bool, str]:
    settings = get_settings()
    if settings.rate_limit_backend != "redis" and settings.case_lock_backend != "redis":
        return True, "not_configured"
    client = await get_redis()
    if client is None:
        return False, "unreachable"
    await client.ping()
    return True, "connected"


async def _erp() -> tuple[bool, str]:
    from ..erp.client import ErpClient

    client = ErpClient()
    ok = await client.health()
    return ok, "healthy" if ok else "unreachable"


async def run_readiness(session: AsyncSession) -> dict:
    settings = get_settings()
    timeout = settings.readiness_timeout_secs
    results = await asyncio.gather(
        _run("database", _database(session), timeout),
        _run("storage", _storage(), timeout),
        _run("redis", _redis(), timeout),
        _run("erp", _erp(), timeout),
    )
    checks = {name: asdict(result) for name, result in results}

    if not checks["database"]["ok"]:
        status = "unavailable"
    elif all(item["ok"] for item in checks.values()):
        status = "ready"
    else:
        status = "degraded"

    return {
        "status": status,
        "environment": settings.environment,
        "version": settings.app_version,
        "checks": checks,
        "warnings": settings.production_warnings(),
    }
