"""API 层 Pydantic Schemas。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from packages.core.models import CaseStatus


class CaseCreate(BaseModel):
    employee_id: str = Field(min_length=1, max_length=64)
    category: str = Field(pattern="^(travel|meal|office_supplies)$")
    amount: float = Field(gt=0)
    description: str = ""


class CaseOut(BaseModel):
    id: str
    employee_id: str
    category: str
    amount: float
    description: str
    status: str
    risk_level: str | None
    state_version: int
    trace_id: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TraceEventOut(BaseModel):
    id: str
    trace_id: str
    case_id: str
    span_id: str | None = None
    parent_span_id: str | None = None
    event_type: str
    actor: str | None = None
    tool_name: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    duration_ms: int | None = None
    error_code: str | None = None
    payload_json: dict = {}
    created_at: datetime

    model_config = {"from_attributes": True}


class ApprovalCreate(BaseModel):
    action: str = Field(pattern="^(approve|reject|request_info|modify_fields|terminate)$")
    comment: str = ""


class ApprovalOut(BaseModel):
    id: str
    case_id: str
    reviewer_id: str
    action: str
    comment: str
    created_at: datetime

    model_config = {"from_attributes": True}


class RunRequest(BaseModel):
    """启动/重跑工作流。"""

    auto_submit: bool = Field(
        default=False,
        description="是否允许满足条件的低风险案件自动提交 ERP（否则停在人工确认）",
    )
    mode: str = Field(
        default="single_agent_verifier",
        pattern="^(fixed_workflow|single_agent|single_agent_verifier|single_agent_verifier_researcher)$",
        description="对照实验模式",
    )


class RunResponse(BaseModel):
    case_id: str
    status: str
    detail: str = ""
    decision: dict[str, Any] = {}
    risk_level: str = ""
    risk_score: float = 0.0
    agent_used: bool = False
    agent_triggers: list[str] = []
    rule_failures: list[str] = []
    verifier_status: str = ""
    verifier_reason: str = ""
    needs_approval: bool = False
    auto_submit_eligible: bool = False
    erp: dict[str, Any] | None = None
    transitions: list[str] = []
    errors: list[str] = []


class CaseDetail(CaseOut):
    """案件详情聚合（文档 §15.2）。"""

    documents: list[dict[str, Any]] = []
    extracted_fields: dict[str, Any] = {}
    evidence: list[dict[str, Any]] = []
    policy_evidence: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    decision: dict[str, Any] | None = None
    approvals: list[dict[str, Any]] = []
    erp_transaction: dict[str, Any] | None = None
    trace_url: str = ""
    allowed_events: list[str] = []
    missing_documents: list[str] = []


class StatusInfo(BaseModel):
    statuses: list[str]
    terminal: list[str]


__all__ = [
    "ApprovalCreate",
    "ApprovalOut",
    "CaseCreate",
    "CaseDetail",
    "CaseOut",
    "CaseStatus",
    "RunRequest",
    "RunResponse",
    "StatusInfo",
    "TraceEventOut",
]
