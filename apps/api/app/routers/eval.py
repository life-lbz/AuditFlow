"""评测 API（文档 §19、§15：POST /eval/runs、GET /eval/runs/{id}）。"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.db import get_session
from packages.core.models import EvalCase, EvalRun
from packages.gateway.factory import get_gateway
from packages.evals.runner import EvalRunner

router = APIRouter(tags=["eval"])


class EvalRunRequest(BaseModel):
    suite: str = "auditflow-v1"
    mode: Literal[
        "fixed_workflow",
        "single_agent",
        "single_agent_verifier",
        "single_agent_verifier_researcher",
    ] = "single_agent_verifier"
    limit: int = Field(default=30, ge=1, le=120, description="同步执行上限 120 条；全量请用 CLI")
    auto_submit: bool = False
    tags: list[str] | None = None


@router.post("/eval/runs")
async def start_eval_run(
    payload: EvalRunRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """执行一次评测并返回完整指标。

    说明：为保持接口简单，这里同步执行（受 limit 约束）。
    全量 120 条及 4 组对照实验请使用：
        python -m packages.evals.runner --compare --out data/eval_report.json
    """
    runner = EvalRunner(session, get_gateway())
    report = await runner.run(
        suite=payload.suite,
        mode=payload.mode,
        limit=payload.limit,
        auto_submit=payload.auto_submit,
        tags=payload.tags,
    )
    if report.errors and report.metrics.total == 0:
        raise HTTPException(status_code=400, detail="; ".join(report.errors[:3]))
    return report.as_dict()


@router.get("/eval/runs")
async def list_eval_runs(
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rows = (
        await session.execute(select(EvalRun).order_by(EvalRun.created_at.desc()).limit(limit))
    ).scalars().all()
    return {
        "runs": [
            {
                "id": r.id,
                "suite": r.suite,
                "agent_mode": r.agent_mode,
                "metrics": r.metrics_json,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }


@router.get("/eval/runs/{run_id}")
async def get_eval_run(run_id: str, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    run = await session.get(EvalRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"评测记录不存在: {run_id}")
    return {
        "id": run.id,
        "suite": run.suite,
        "agent_mode": run.agent_mode,
        "metrics": run.metrics_json,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


@router.get("/eval/cases")
async def list_eval_cases(
    suite: str = Query(default="auditflow-v1"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(EvalCase)
            .where(EvalCase.suite == suite)
            .order_by(EvalCase.id)
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    total = len(
        (await session.execute(select(EvalCase.id).where(EvalCase.suite == suite))).scalars().all()
    )
    return {
        "suite": suite,
        "total": total,
        "cases": [
            {
                "id": c.id,
                "category": (c.input_json or {}).get("category"),
                "amount": (c.input_json or {}).get("amount"),
                "description": (c.input_json or {}).get("description"),
                "expected": c.expected_json,
                "tags": c.tags,
            }
            for c in rows
        ],
    }
