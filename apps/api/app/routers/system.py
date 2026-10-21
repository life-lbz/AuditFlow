"""系统信息、健康检查、令牌签发与指标端点。"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.auth import create_token
from packages.core.config import get_settings
from packages.core.db import get_session
from packages.core.metrics import render as render_metrics
from packages.core.models import Case, CaseStatus, ErpTransaction, PolicyChunk, TraceEvent
from packages.core.state_machine import TERMINAL_STATES
from packages.core.tracing import EVENT_VERIFIER_FINISHED
from packages.erp.client import ErpClient
from packages.gateway.factory import get_gateway

router = APIRouter(tags=["system"])


class TokenRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=64, description="用户标识")
    role: Literal["employee", "reviewer", "admin"] = "employee"
    ttl_seconds: int | None = Field(default=None, ge=60, le=86400)


@router.post("/auth/token")
async def issue_token(payload: TokenRequest) -> dict[str, Any]:
    """签发访问令牌（仅 AUTH_MODE=dev 开放；生产由企业 SSO 或 CLI 签发）。"""
    settings = get_settings()
    if (settings.auth_mode or "dev").strip().lower() != "dev":
        raise HTTPException(
            status_code=403,
            detail="当前为 token 鉴权模式，请使用企业 SSO 或 python -m packages.core.auth 签发",
        )
    ttl = payload.ttl_seconds or settings.auth_token_ttl_secs
    return {
        "access_token": create_token(payload.user_id, payload.role, ttl_seconds=ttl),
        "token_type": "bearer",
        "expires_in": ttl,
        "role": payload.role,
    }


@router.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok"}


@router.get("/system/info")
async def system_info(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """运行环境与数据概览（前端首页使用）。"""
    settings = get_settings()
    total_cases = (await session.execute(select(func.count(Case.id)))).scalar() or 0
    by_status_rows = (
        await session.execute(select(Case.status, func.count(Case.id)).group_by(Case.status))
    ).all()
    policy_count = (await session.execute(select(func.count(PolicyChunk.id)))).scalar() or 0

    return {
        "model_provider": settings.model_provider,
        "storage_backend": settings.storage_backend,
        "mock_erp_url": settings.mock_erp_url,
        "minio_bucket": settings.minio_bucket,
        "statuses": [s.value for s in CaseStatus],
        "terminal_statuses": [s.value for s in TERMINAL_STATES],
        "cases": {"total": total_cases, "by_status": {r[0]: r[1] for r in by_status_rows}},
        "policy_chunks": policy_count,
        "budgets": {
            "agent_max_tool_calls": settings.agent_max_tool_calls,
            "agent_max_replans": settings.agent_max_replans,
            "agent_max_steps": settings.agent_max_steps,
            "case_timeout_secs": settings.case_timeout_secs,
        },
        "retrieval": {
            "keyword_k": settings.retrieval_keyword_k,
            "vector_k": settings.retrieval_vector_k,
            "fusion_k": settings.retrieval_fusion_k,
            "rerank_k": settings.retrieval_rerank_k,
        },
        "risk_thresholds": {
            "low": settings.risk_low_threshold,
            "high": settings.risk_high_threshold,
        },
    }


@router.get("/system/model")
async def model_health() -> dict[str, Any]:
    """模型网关健康检查（本地 stub 或远端 OpenAI 兼容服务）。"""
    gateway = get_gateway()
    return await gateway.health()


@router.get("/system/erp")
async def erp_health() -> dict[str, Any]:
    client = ErpClient()
    ok = await client.health()
    return {"ok": ok, "url": get_settings().mock_erp_url}


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(session: AsyncSession = Depends(get_session)) -> str:
    """Prometheus 文本指标：进程内 HTTP 指标 + 数据库业务指标。"""
    status_rows = (
        await session.execute(select(Case.status, func.count(Case.id)).group_by(Case.status))
    ).all()
    erp_rows = (
        await session.execute(
            select(ErpTransaction.status, func.count(ErpTransaction.id)).group_by(
                ErpTransaction.status
            )
        )
    ).all()
    policy_count = (await session.execute(select(func.count(PolicyChunk.id)))).scalar() or 0
    verifier_payloads = (
        await session.execute(
            select(TraceEvent.payload_json).where(TraceEvent.event_type == EVENT_VERIFIER_FINISHED)
        )
    ).scalars().all()
    verifier_counts: dict[str, int] = {}
    for payload in verifier_payloads:
        if isinstance(payload, dict):
            status = str(payload.get("status", "unknown"))
            verifier_counts[status] = verifier_counts.get(status, 0) + 1

    extra = [
        "# HELP auditflow_cases_total 案件总数（按状态）",
        "# TYPE auditflow_cases_total gauge",
        *[f'auditflow_cases_total{{status="{status}"}} {count}' for status, count in status_rows],
        "# HELP auditflow_policy_chunks_total 制度切片总数",
        "# TYPE auditflow_policy_chunks_total gauge",
        f"auditflow_policy_chunks_total {policy_count}",
        "# HELP auditflow_erp_transactions_total ERP 事务数（按状态）",
        "# TYPE auditflow_erp_transactions_total gauge",
        *[
            f'auditflow_erp_transactions_total{{status="{status}"}} {count}'
            for status, count in erp_rows
        ],
        "# HELP auditflow_verifier_runs_total Verifier 执行次数（按结果）",
        "# TYPE auditflow_verifier_runs_total counter",
        *[
            f'auditflow_verifier_runs_total{{status="{status}"}} {count}'
            for status, count in sorted(verifier_counts.items())
        ],
    ]
    return render_metrics(extra=extra)
