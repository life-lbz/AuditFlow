"""人工审核 API（文档 §13：转人工、审批动作、审批记录）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.db import get_session
from packages.core.models import Approval, Case, CaseStatus
from packages.core.permissions import Action
from packages.core.state_machine import (
    CaseEvent,
    GuardFailedError,
    OptimisticLockError,
    TransitionError,
    transition,
)
from packages.core.tracing import EVENT_APPROVAL_RECEIVED, emit_trace, new_id
from packages.gateway.factory import get_gateway
from packages.orchestrator.orchestrator import CaseOrchestrator

from ..deps import get_role, get_user_id, require
from ..schemas.case import ApprovalCreate, ApprovalOut, CaseOut

router = APIRouter(tags=["approvals"])

# 审批动作 -> 状态机事件（terminate 与 reject 都终结案件，但审批记录保留原始动作语义）
_ACTION_TO_EVENT = {
    "approve": CaseEvent.APPROVED,
    "reject": CaseEvent.REJECTED,
    "request_info": CaseEvent.EVIDENCE_MISSING,
    "terminate": CaseEvent.REJECTED,
}


@router.get("/approvals/pending", response_model=list[CaseOut])
async def list_pending(
    session: AsyncSession = Depends(get_session),
    role=Depends(get_role),
) -> list[CaseOut]:
    """待审核案件列表（审核员与管理员可见）。"""
    require(role, Action.VIEW_PENDING_CASES)
    rows = (
        await session.execute(
            select(Case)
            .where(Case.status.in_([CaseStatus.WAITING_APPROVAL.value, CaseStatus.ESCALATED.value]))
            .order_by(Case.created_at)
        )
    ).scalars().all()
    return [CaseOut.model_validate(c) for c in rows]


@router.get("/cases/{case_id}/approvals", response_model=list[ApprovalOut])
async def list_approvals(
    case_id: str,
    session: AsyncSession = Depends(get_session),
    role=Depends(get_role),
) -> list[ApprovalOut]:
    require(role, Action.VIEW_TRACE)
    rows = (
        await session.execute(
            select(Approval).where(Approval.case_id == case_id).order_by(Approval.created_at)
        )
    ).scalars().all()
    return [ApprovalOut.model_validate(a) for a in rows]


@router.post("/cases/{case_id}/approvals", status_code=201)
async def submit_approval(
    case_id: str,
    payload: ApprovalCreate,
    session: AsyncSession = Depends(get_session),
    role=Depends(get_role),
    user_id=Depends(get_user_id),
) -> dict[str, Any]:
    """提交审批动作（文档 §13.3：批准并提交 / 驳回 / 要求补材料 / 修改字段 / 终止）。"""
    require(role, Action.APPROVE_REJECT)
    case = await session.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"案件不存在: {case_id}")
    if case.status not in {CaseStatus.WAITING_APPROVAL.value, CaseStatus.ESCALATED.value}:
        raise HTTPException(
            status_code=409,
            detail=f"案件当前状态 {case.status} 不在待审核环节，无法提交审批",
        )

    session.add(
        Approval(
            id=new_id("appr"),
            case_id=case_id,
            reviewer_id=user_id,
            action=payload.action,
            comment=payload.comment,
        )
    )
    await emit_trace(
        session,
        case_id=case_id,
        trace_id=case.trace_id,
        event_type=EVENT_APPROVAL_RECEIVED,
        actor=user_id,
        payload={"action": payload.action, "comment": payload.comment, "role": role.value},
    )

    # modify_fields：仅记录意见，要求员工/审核员修改后重新运行
    if payload.action == "modify_fields":
        await session.commit()
        return {
            "case_id": case_id,
            "action": "modify_fields",
            "status": case.status,
            "detail": "已记录修改意见，请修改材料或字段后调用 /cases/{id}/retry 重新运行",
        }

    event = _ACTION_TO_EVENT[payload.action]
    try:
        case = await transition(
            session,
            case_id,
            event,
            expected_version=case.state_version,
            actor=user_id,
            context={"actor_role": role.value},
        )
    except TransitionError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GuardFailedError as exc:
        await session.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except OptimisticLockError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await session.commit()
    await session.refresh(case)

    result: dict[str, Any] = {
        "case_id": case_id,
        "action": payload.action,
        "status": case.status,
        "detail": "",
    }

    # 批准后立即执行 ERP（文档 §13.3「批准并提交 ERP」）
    if payload.action == "approve":
        orchestrator = CaseOrchestrator(session, get_gateway(), actor=user_id)
        run = await orchestrator.execute_after_approval(
            case_id, approved_by=user_id, auto_submit=True
        )
        await session.refresh(case)
        result.update(
            {
                "status": case.status,
                "erp": run.erp,
                "errors": run.errors,
                "detail": "已批准并提交 ERP",
            }
        )
    elif payload.action == "request_info":
        result["detail"] = "已要求补充材料，案件进入 NEEDS_INFO"
    else:
        result["detail"] = "案件已驳回并终结"

    return result
