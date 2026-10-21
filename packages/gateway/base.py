"""模型网关抽象（文档 §17：统一接口 / 记录内容 / 结构化输出 / 降级）。

设计意图：
- 所有依赖大模型的能力（文档抽取、决策、验证、embedding、rerank）统一经由 ModelGateway，
  业务代码不直接调用任何模型 SDK。
- 本地默认使用 StubModelGateway（确定性、可测试、零外部依赖），保证全流程可端到端跑通。
- 迁移到 AutoDL 等 GPU 环境时，把 MODEL_PROVIDER 切为 openai_compatible 即可接入真实模型，
  业务代码零改动。
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class DocumentType(str, enum.Enum):
    """支持的文档类型（文档 §7.2）。"""

    INVOICE = "invoice"
    RECEIPT = "receipt"
    BANK_STATEMENT = "bank_statement"
    APPROVAL_EMAIL = "approval_email"
    POLICY_PDF = "policy_pdf"
    UNKNOWN = "unknown"


@dataclass
class ModelCallRecord:
    """每次模型调用的记录（文档 §17.2）。

    该记录会写入 TraceEvent，用于成本、延迟与降级分析。
    """

    provider: str
    model: str
    prompt_version: str = "v1"
    input_hash: str = ""
    output_hash: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    retry_count: int = 0
    error: str | None = None
    degraded: bool = False

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "latency_ms": self.latency_ms,
            "cost_usd": self.cost_usd,
            "retry_count": self.retry_count,
            "error": self.error,
            "degraded": self.degraded,
        }


@dataclass
class ExtractionField:
    """单个抽取字段（文档 §7.3：value + confidence + 证据区域）。"""

    value: Any
    confidence: float = 0.0
    page_no: int = 1
    bbox: list[int] | None = None
    source: str = "vlm"  # ocr / vlm / fused


@dataclass
class ExtractionRequest:
    """结构化抽取请求。"""

    document_type: DocumentType
    pages: list[str] = field(default_factory=list)  # 每页 OCR 文本
    image_keys: list[str | None] = field(default_factory=list)
    hint_schema: dict[str, str] = field(default_factory=dict)


@dataclass
class ExtractionResult:
    """结构化抽取结果（文档 §7.3）。"""

    document_type: DocumentType
    fields: dict[str, ExtractionField] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    call: ModelCallRecord | None = None
    degraded: bool = False
    error: str | None = None

    def value_of(self, name: str) -> Any:
        f = self.fields.get(name)
        return f.value if f else None

    def confidence_of(self, name: str) -> float:
        f = self.fields.get(name)
        return f.confidence if f else 0.0

    def min_confidence(self, names: list[str] | None = None) -> float:
        keys = names or list(self.fields)
        vals = [self.fields[k].confidence for k in keys if k in self.fields]
        return min(vals) if vals else 0.0


@dataclass
class AgentAction:
    """Decision Agent 的一步动作（文档 §10.3）。

    type == "tool"  ：调用工具，携带 tool_name 与 arguments
    type == "final" ：给出最终结论，payload 为 DecisionPayload 字典
    """

    type: str
    tool_name: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""
    call: ModelCallRecord | None = None

    @classmethod
    def call_tool(cls, tool_name: str, arguments: dict[str, Any], rationale: str = "") -> AgentAction:
        return cls(type="tool", tool_name=tool_name, arguments=arguments, rationale=rationale)

    @classmethod
    def final(cls, payload: dict[str, Any], rationale: str = "") -> AgentAction:
        return cls(type="final", payload=payload, rationale=rationale)


@dataclass
class VerificationResult:
    """Verifier 输出（文档 §12.2）。"""

    status: str  # passed / failed
    reason: str = ""
    blocking: bool = False
    failed_checks: list[str] = field(default_factory=list)
    suggested_action: str = ""  # retry_agent_once / escalate_human / none
    call: ModelCallRecord | None = None

    @classmethod
    def passed_result(cls, reason: str = "") -> VerificationResult:
        return cls(status="passed", reason=reason)

    @classmethod
    def failed_result(
        cls,
        reason: str,
        failed_checks: list[str] | None = None,
        blocking: bool = True,
        suggested_action: str = "escalate_human",
    ) -> VerificationResult:
        return cls(
            status="failed",
            reason=reason,
            blocking=blocking,
            failed_checks=failed_checks or [],
            suggested_action=suggested_action,
        )


@dataclass
class VerificationRequest:
    """模型验证请求。"""

    case_summary: dict[str, Any] = field(default_factory=dict)
    decision: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    failed_checks: list[dict[str, Any]] = field(default_factory=list)
    policy_quotes: list[dict[str, Any]] = field(default_factory=list)


class ModelGateway(ABC):
    """模型网关统一接口（文档 §17.1）。"""

    provider: str = "base"

    @abstractmethod
    async def extract_structured(self, request: ExtractionRequest) -> ExtractionResult:
        """文档结构化抽取。"""

    @abstractmethod
    async def choose_action(
        self,
        state: dict[str, Any],
        tools: list[dict[str, Any]],
    ) -> AgentAction:
        """在给定状态与可用工具下选择下一步动作。"""

    @abstractmethod
    async def verify(self, request: VerificationRequest) -> VerificationResult:
        """结论与证据的一致性验证。"""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """文本向量化。"""

    @abstractmethod
    async def rerank(self, query: str, candidates: list[str], top_k: int = 5) -> list[tuple[int, float]]:
        """候选重排，返回 (原始下标, 分数) 列表。"""

    async def health(self) -> dict[str, Any]:
        return {"provider": self.provider, "ok": True}
