"""Decision Agent 的数据契约（文档 §10.2 输入状态、附录 A 输出 Schema）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DecisionType = Literal["approve", "need_info", "escalate", "reject"]
RiskLevelType = Literal["low", "medium", "high"]


class DecisionPayload(BaseModel):
    """Agent 最终输出（文档附录 A，所有字段必填）。"""

    model_config = ConfigDict(extra="forbid")

    decision: DecisionType
    risk_level: RiskLevelType
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    failed_checks: list[str] = Field(default_factory=list)
    missing_items: list[str] = Field(default_factory=list)
    proposed_actions: list[str] = Field(default_factory=list)

    @field_validator("summary")
    @classmethod
    def _summary_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("summary 不能为空白")
        return v.strip()

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump()


class DecisionEnvelope(BaseModel):
    """包裹层：允许模型输出 decision 定义，但统一走校验。"""

    model_config = ConfigDict(extra="ignore")

    decision: DecisionType
    risk_level: RiskLevelType
    confidence: float = 0.5
    summary: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    failed_checks: list[str] = Field(default_factory=list)
    missing_items: list[str] = Field(default_factory=list)
    proposed_actions: list[str] = Field(default_factory=list)


@dataclass
class AgentState:
    """Agent 每轮接收的案件状态快照（文档 §10.2）。"""

    case: dict[str, Any] = field(default_factory=dict)
    extracted_fields: dict[str, Any] = field(default_factory=dict)
    failed_checks: list[dict[str, Any]] = field(default_factory=list)
    candidate_evidence: list[dict[str, Any]] = field(default_factory=list)
    previous_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    remaining_budget: dict[str, int] = field(default_factory=dict)
    document_types: list[str] = field(default_factory=list)
    missing_documents: list[str] = field(default_factory=list)
    min_extraction_confidence: float = 1.0
    low_confidence_documents: list[str] = field(default_factory=list)
    policy_limits: dict[str, float] = field(default_factory=dict)
    risk_hint: str = "medium"

    def as_model_view(self) -> dict[str, Any]:
        """序列化给模型（不含内部实现细节）。"""
        return {
            "case": self.case,
            "extracted_fields": self.extracted_fields,
            "failed_checks": self.failed_checks,
            "candidate_evidence": [
                {
                    "evidence_id": e.get("evidence_id"),
                    "source_type": e.get("source_type"),
                    "section": e.get("section"),
                    "quote": (e.get("quote") or "")[:160],
                    "score": e.get("score"),
                }
                for e in self.candidate_evidence
            ],
            "previous_tool_calls": self.previous_tool_calls,
            "allowed_tools": self.allowed_tools,
            "remaining_budget": self.remaining_budget,
            "document_types": self.document_types,
            "missing_documents": self.missing_documents,
            "min_extraction_confidence": self.min_extraction_confidence,
            "low_confidence_documents": self.low_confidence_documents,
            "policy_limits": self.policy_limits,
        }


@dataclass
class AgentRunResult:
    """Agent 循环的最终结果。"""

    decision: DecisionPayload
    steps: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    exhausted: bool = False
    replans: int = 0
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.as_dict(),
            "steps": self.steps,
            "tool_calls": self.tool_calls,
            "exhausted": self.exhausted,
            "replans": self.replans,
            "error": self.error,
        }


class PolicyViolation(Exception):
    """Agent 尝试调用未授权工具（文档 §10.3）。"""
