"""Trace 写入工具（文档 §18：所有关键动作可追踪、可回放）。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .models import TraceEvent

# 事件类型常量（文档 §18.1）
EVENT_CASE_CREATED = "case_created"
EVENT_DOCUMENT_UPLOADED = "document_uploaded"
EVENT_DOCUMENT_EXTRACTED = "document_extracted"
EVENT_POLICY_RETRIEVED = "policy_retrieved"
EVENT_RULE_EVALUATED = "rule_evaluated"
EVENT_AGENT_STEP = "agent_step"
EVENT_TOOL_COMPLETED = "tool_completed"
EVENT_DECISION_MADE = "decision_made"
EVENT_VERIFIER_FINISHED = "verifier_finished"
EVENT_APPROVAL_RECEIVED = "approval_received"
EVENT_ERP_EXECUTED = "erp_executed"
EVENT_CASE_COMPLETED = "case_completed"
EVENT_MODEL_CALL = "model_call"
EVENT_ERROR = "error"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


async def emit_trace(
    session: AsyncSession,
    *,
    case_id: str,
    trace_id: str | None = None,
    event_type: str,
    actor: str | None = None,
    payload: dict[str, Any] | None = None,
    span_id: str | None = None,
    parent_span_id: str | None = None,
    tool_name: str | None = None,
    duration_ms: int | None = None,
    error_code: str | None = None,
    model_record: Any | None = None,
    commit: bool = False,
) -> TraceEvent:
    """写入一条 Trace 事件。

    model_record 为 gateway.base.ModelCallRecord 时，自动展开模型调用字段（文档 §17.2）。
    """
    model_fields: dict[str, Any] = {}
    if model_record is not None:
        model_fields = {
            "model": getattr(model_record, "model", None),
            "model_version": getattr(model_record, "model", None),
            "prompt_version": getattr(model_record, "prompt_version", None),
            "input_hash": getattr(model_record, "input_hash", None) or None,
            "output_hash": getattr(model_record, "output_hash", None) or None,
        }
        if payload is None:
            payload = {}
        payload = {**payload, "model_call": model_record.as_dict()}

    event = TraceEvent(
        id=new_id("evt"),
        trace_id=trace_id or f"trace_{case_id}",
        case_id=case_id,
        span_id=span_id or new_id("span"),
        parent_span_id=parent_span_id,
        event_type=event_type,
        actor=actor,
        tool_name=tool_name,
        duration_ms=duration_ms,
        error_code=error_code,
        payload_json=payload or {},
        **model_fields,
    )
    session.add(event)
    if commit:
        await session.commit()
    return event


async def list_trace(
    session: AsyncSession,
    case_id: str,
) -> list[TraceEvent]:
    """按时间顺序返回案件的全部 Trace 事件（文档 §18.2：可以按顺序重放整个案件）。"""
    from sqlalchemy import select

    result = await session.execute(
        select(TraceEvent).where(TraceEvent.case_id == case_id).order_by(TraceEvent.created_at, TraceEvent.id)
    )
    return list(result.scalars().all())
