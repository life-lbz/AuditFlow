"""AuditFlow API 服务入口（文档 §4、§15、§26）。

挂载的能力：
- 案件：创建 / 列表 / 详情 / 上传文档 / 运行 / 重试 / 轨迹 / 回放
- 审批：待审列表 / 提交审批动作 / 审批记录
- 制度：清单 / 混合检索调试 / 切片查看
- 评测：启动评测 / 结果查询 / 数据集浏览
- 系统：健康检查 / 运行环境 / 模型网关 / Mock ERP
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from packages.core.config import get_settings
from packages.core.metrics import registry as metrics_registry
from packages.core.permissions import PermissionDenied
from packages.core.storage import get_storage
from packages.core.tracing import new_id
from packages.documents.parser import DocumentParseError

from .app.routers import approvals, cases, eval as eval_router, policies, system

logger = logging.getLogger("auditflow.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时确保对象存储可用；数据库结构由 Alembic 管理。"""
    settings = get_settings()
    try:
        await get_storage().ensure_ready()
        logger.info("storage ready: backend=%s", settings.storage_backend)
    except Exception as exc:  # noqa: BLE001 - 存储不可用不阻塞 API 启动，但明确告警
        logger.warning("storage init failed: %s", exc)
    logger.info(
        "AuditFlow API started: model_provider=%s mock_erp=%s",
        settings.model_provider,
        settings.mock_erp_url,
    )
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AuditFlow API",
        version="0.1.0",
        description="多模态财务审核智能体：确定性工作流 + 单 Decision Agent + 独立 Verifier + 人工审批",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        # 浏览器规范不允许 allow_origins=["*"] 与 credentials 同时开启
        allow_credentials=settings.cors_origin_list != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        """为每个请求分配 request_id，记录访问日志与指标，并写入响应头。"""
        request_id = request.headers.get("X-Request-ID") or new_id("req")
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        metrics_registry.observe(request.method, path, response.status_code, elapsed_ms)
        logger.info(
            "request method=%s path=%s status=%s duration_ms=%.1f request_id=%s",
            request.method,
            path,
            response.status_code,
            elapsed_ms,
            request_id,
        )
        response.headers["X-Request-ID"] = request_id
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
