"""AuditFlow API 服务入口（文档 §4、§15、§26）。

挂载的能力：
- 案件：创建 / 列表 / 详情 / 上传文档 / 运行 / 重试 / 轨迹 / 回放
- 审批：待审列表 / 提交审批动作 / 审批记录
- 制度：清单 / 混合检索调试 / 切片查看
- 评测：启动评测 / 结果查询 / 数据集浏览
- 系统：存活/就绪探针 / 运行环境 / 模型网关 / Mock ERP / 指标

生产基线：安全响应头、请求体上限、限流、结构化日志、生产配置自检、优雅关闭。
"""

from __future__ import annotations

import logging
import re
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from packages.core.config import get_settings
from packages.core.db import engine
from packages.core.locks import LockBusy
from packages.core.logging import configure_logging
from packages.core.metrics import registry as metrics_registry
from packages.core.permissions import PermissionDenied
from packages.core.ratelimit import limiter
from packages.core.redis_client import close_redis
from packages.core.storage import get_storage
from packages.core.tracing import new_id
from packages.documents.parser import DocumentParseError

from .app.routers import approvals, cases, eval as eval_router, policies, system

logger = logging.getLogger("auditflow.api")

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_RATE_LIMIT_EXEMPT = ("/health", "/metrics")


def _client_ip(request: Request) -> str:
    """仅当明确配置信任反代时才读取 X-Real-IP，避免请求头伪造绕过限流。"""
    settings = get_settings()
    if settings.trust_proxy_headers:
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()[:64]
    return request.client.host if request.client else "unknown"


def _security_headers(settings) -> dict[str, str]:
    if not settings.security_headers_enabled:
        return {}
    return {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动自检与资源释放。数据库结构由 Alembic 在容器启动命令中迁移。"""
    settings = get_settings()
    errors = settings.production_errors()
    if errors:
        raise RuntimeError("生产配置未通过安全自检: " + "；".join(errors))
    for warning in settings.production_warnings():
        logger.warning("production baseline warning: %s", warning)

    try:
        await get_storage().ensure_ready()
        logger.info("storage ready: backend=%s", settings.storage_backend)
    except Exception as exc:  # noqa: BLE001 - 存储不可用不阻塞 API 启动，但明确告警
        logger.warning("storage init failed: %s", exc)

    logger.info(
        "AuditFlow API started: version=%s environment=%s model_provider=%s rate_limit=%s",
        settings.app_version,
        settings.environment,
        settings.model_provider,
        settings.rate_limit_active,
    )
    yield
    await close_redis()
    await engine.dispose()
    logger.info("AuditFlow API stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    docs_enabled = settings.docs_enabled
    app = FastAPI(
        title="AuditFlow API",
        version=settings.app_version,
        description="多模态财务审核智能体：确定性工作流 + 单 Decision Agent + 独立 Verifier + 人工审批",
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        # 浏览器规范不允许 allow_origins=["*"] 与 credentials 同时开启
        allow_credentials=settings.cors_origin_list != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        """请求 ID → 请求体上限 → 限流 → 业务处理 → 指标/日志/安全头。"""
        incoming_id = request.headers.get("X-Request-ID") or ""
        request_id = incoming_id if _REQUEST_ID_PATTERN.match(incoming_id) else new_id("req")
        request.state.request_id = request_id
        headers = _security_headers(settings)

        # 1) 请求体上限（chunked 请求由 nginx client_max_body_size 兜底）
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit():
            if int(content_length) > settings.max_request_bytes:
                return JSONResponse(
                    status_code=413,
                    content={
                        "code": "payload_too_large",
                        "message": f"请求体超过 {settings.max_request_mb}MB 上限",
                        "detail": f"请求体超过 {settings.max_request_mb}MB 上限",
                        "request_id": request_id,
                    },
                    headers={**headers, "X-Request-ID": request_id},
                )

        # 2) 限流（按来源 IP + 读写分组，健康检查与指标端点豁免）
        rate_headers: dict[str, str] = {}
        path = request.url.path
        if (
            settings.rate_limit_active
            and request.method != "OPTIONS"
            and not path.startswith(_RATE_LIMIT_EXEMPT)
        ):
            write = request.method in _WRITE_METHODS
            limit = (
                settings.rate_limit_writes_per_minute
                if write
                else settings.rate_limit_requests_per_minute
            )
            result = await limiter.hit(
                "write" if write else "read", _client_ip(request), limit
            )
            rate_headers = {
                "X-RateLimit-Limit": str(result.limit),
                "X-RateLimit-Remaining": str(result.remaining),
                "X-RateLimit-Reset": str(result.reset_after),
            }
            if not result.allowed:
                logger.warning(
                    "rate limited",
                    extra={"request_id": request_id, "method": request.method, "path": path},
                )
                return JSONResponse(
                    status_code=429,
                    content={
                        "code": "rate_limited",
                        "message": "请求过于频繁，请稍后重试",
                        "detail": "请求过于频繁，请稍后重试",
                        "request_id": request_id,
                        "retry_after": result.reset_after,
                    },
                    headers={
                        **headers,
                        **rate_headers,
                        "Retry-After": str(result.reset_after),
                        "X-Request-ID": request_id,
                    },
                )

        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        route = request.scope.get("route")
        route_path = getattr(route, "path", path)
        metrics_registry.observe(request.method, route_path, response.status_code, elapsed_ms)
        logger.info(
            "request completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": route_path,
                "status": response.status_code,
                "duration_ms": round(elapsed_ms, 1),
            },
        )
        response.headers["X-Request-ID"] = request_id
        for key, value in headers.items():
            response.headers.setdefault(key, value)
        for key, value in rate_headers.items():
            response.headers.setdefault(key, value)
        return response

    app.include_router(cases.router)
    app.include_router(approvals.router)
    app.include_router(policies.router)
    app.include_router(eval_router.router)
    app.include_router(system.router)

    def _error(request: Request, status_code: int, code: str, message: str) -> JSONResponse:
        """统一错误结构；detail 字段保留以兼容现有前端错误提示。"""
        request_id = getattr(request.state, "request_id", "")
        return JSONResponse(
            status_code=status_code,
            content={"code": code, "message": message, "detail": message, "request_id": request_id},
        )

    @app.exception_handler(PermissionDenied)
    async def permission_denied_handler(request: Request, exc: PermissionDenied) -> JSONResponse:
        logger.warning("permission denied on %s %s: %s", request.method, request.url.path, exc)
        return _error(request, 403, "permission_denied", str(exc) or "无权限执行该操作")

    @app.exception_handler(DocumentParseError)
    async def document_parse_handler(request: Request, exc: DocumentParseError) -> JSONResponse:
        return _error(request, 400, "document_parse_error", str(exc))

    @app.exception_handler(LockBusy)
    async def lock_busy_handler(request: Request, exc: LockBusy) -> JSONResponse:
        return _error(request, 409, "case_busy", "该案件正在被其他请求处理，请稍后重试")

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # 只记录堆栈，不把内部异常类型与内容回显给调用方
        logger.exception(
            "unhandled error on %s %s (request_id=%s)",
            request.method,
            request.url.path,
            getattr(request.state, "request_id", ""),
        )
        return _error(request, 500, "internal_error", "服务器内部错误，请稍后重试或联系管理员")

    return app


app = create_app()
