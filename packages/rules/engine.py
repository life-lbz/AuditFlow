"""确定性规则引擎（文档 §9）。

原则（文档 §9.3）：
- 金额、税额、日期、重复检查必须由代码计算，不得由模型推断。
- 规则失败不一定直接拒绝，但必须提升风险等级。
- 规则结果必须进入 Verifier。
- 规则版本随案件轨迹保存。
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.models import Check
from ..core.tracing import EVENT_RULE_EVALUATED, emit_trace, new_id

RULE_VERSION = "1.0"


class RuleStatus(str, enum.Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class Severity(str, enum.Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class EvidenceRef:
    """规则可引用的证据（字段级或制度级）。"""

    evidence_id: str
    source_type: str
    source_id: str
    quote: str = ""
    page_no: int | None = None
    score: float = 0.0


@dataclass
class RuleContext:
    """规则执行上下文（一次案件的全部输入）。"""

    case_id: str
    employee_id: str = ""
    category: str = ""
    amount: float = 0.0
    description: str = ""
    fields: dict[str, Any] = field(default_factory=dict)
    confidences: dict[str, float] = field(default_factory=dict)
    by_document: dict[str, dict[str, Any]] = field(default_factory=dict)
    document_types: list[str] = field(default_factory=list)
    document_ids: dict[str, str] = field(default_factory=dict)  # type -> doc_id
    policy_chunks: list[dict[str, Any]] = field(default_factory=list)
    policy_limits: dict[str, float] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)
    evidence_by_field: dict[str, str] = field(default_factory=dict)
    policy_evidence: list[EvidenceRef] = field(default_factory=list)
    vendor_whitelist: list[str] = field(default_factory=list)
    vendor_blacklist: list[str] = field(default_factory=list)
    today: date = field(default_factory=date.today)

    def field_doc(self, doc_type: str) -> dict[str, Any]:
        """取指定文档类型的字段集。"""
        doc_id = self.document_ids.get(doc_type)
        return self.by_document.get(doc_id, {}) if doc_id else {}

    def evidence_of(self, field_name: str) -> list[str]:
        ev = self.evidence_by_field.get(field_name)
        return [ev] if ev else []


@dataclass
class RuleResult:
    """单条规则结果（文档 §9.2）。"""

    rule_name: str
    status: RuleStatus
    severity: Severity = Severity.NONE
    message: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    rule_version: str = RULE_VERSION
    input_json: dict[str, Any] = field(default_factory=dict)
    result_json: dict[str, Any] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return self.status == RuleStatus.FAILED

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_name": self.rule_name,
            "status": self.status.value,
            "severity": self.severity.value,
            "message": self.message,
            "evidence_ids": self.evidence_ids,
            "rule_version": self.rule_version,
        }


class Rule(ABC):
    """规则基类。"""

    name: str = "base_rule"
    version: str = RULE_VERSION
    description: str = ""

    @abstractmethod
    async def evaluate(self, ctx: RuleContext) -> RuleResult:
        ...

    def skipped(self, message: str, **inputs: Any) -> RuleResult:
        return RuleResult(
            rule_name=self.name,
            status=RuleStatus.SKIPPED,
            message=message,
            rule_version=self.version,
            input_json=inputs,
        )

    def passed(self, message: str, evidence_ids: list[str] | None = None, **extra: Any) -> RuleResult:
        return RuleResult(
            rule_name=self.name,
            status=RuleStatus.PASSED,
            severity=Severity.NONE,
            message=message,
            evidence_ids=evidence_ids or [],
            rule_version=self.version,
            result_json=extra,
        )

    def failed(
        self,
        message: str,
        severity: Severity = Severity.MEDIUM,
        evidence_ids: list[str] | None = None,
        **extra: Any,
    ) -> RuleResult:
        return RuleResult(
            rule_name=self.name,
            status=RuleStatus.FAILED,
            severity=severity,
            message=message,
            evidence_ids=evidence_ids or [],
            rule_version=self.version,
            result_json=extra,
        )


# ---------------------------------------------------------------------------
# 风险评分（文档 §2.3）
# ---------------------------------------------------------------------------

RISK_WEIGHTS = {
    "amount_risk": 0.25,
    "document_mismatch": 0.20,
    "duplicate_suspicion": 0.20,
    "low_extraction_confidence": 0.15,
    "policy_ambiguity": 0.10,
    "vendor_risk": 0.10,
}


@dataclass
class RiskAssessment:
    score: float
    level: str
    components: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"score": round(self.score, 4), "level": self.level, "components": self.components}


def assess_risk(
    ctx: RuleContext,
    results: list[RuleResult],
    *,
    low_threshold: float = 0.30,
    high_threshold: float = 0.60,
) -> RiskAssessment:
    """可解释的风险评分（文档 §2.3 初始公式）。

    各分量均由规则结果与确定性信号推导，便于在评测集上校准权重。
    """
    failed = {r.rule_name: r for r in results if r.failed}

    # 金额风险：相对报销类型额度上限的逼近程度
    limit = ctx.policy_limits.get("category_cap") or ctx.policy_limits.get("travel_daily")
    if limit:
        amount_risk = min(1.0, float(ctx.amount) / float(limit))
    else:
        # 无制度额度时退化为绝对金额分段（缺制度本身也会体现在 policy_ambiguity）
        amount_risk = min(1.0, float(ctx.amount) / 5000.0)

    # 文档不一致：金额类规则失败
    mismatch_rules = {"amount_matches_receipt", "amount_matches_bank", "date_in_range"}
    document_mismatch = min(1.0, 0.6 * len([r for n, r in failed.items() if n in mismatch_rules]))

    # 重复嫌疑
    duplicate_suspicion = 1.0 if "duplicate_invoice" in failed else 0.0

    # 抽取置信度
    confs = list(ctx.confidences.values())
    min_conf = min(confs) if confs else 1.0
    low_extraction_confidence = max(0.0, min(1.0, (0.9 - min_conf) / 0.5))

    # 制度是否可引用（无命中制度视为歧义）
    policy_ambiguity = 0.0 if ctx.policy_chunks else 1.0

    # 供应商风险
    vendor_risk = 1.0 if "vendor_check" in failed else 0.0

    components = {
        "amount_risk": round(amount_risk, 4),
        "document_mismatch": round(document_mismatch, 4),
        "duplicate_suspicion": round(duplicate_suspicion, 4),
        "low_extraction_confidence": round(low_extraction_confidence, 4),
        "policy_ambiguity": round(policy_ambiguity, 4),
        "vendor_risk": round(vendor_risk, 4),
    }
    score = sum(RISK_WEIGHTS[k] * v for k, v in components.items())

    # 高风险规则失败直接抬到 high（文档 §9.3：规则失败必须提升风险等级）
    has_high = any(r.severity == Severity.HIGH for r in results if r.failed)
    if has_high:
        score = max(score, high_threshold)
        level = "high"
    elif score >= high_threshold:
        level = "high"
    elif score >= low_threshold:
        level = "medium"
    else:
        level = "low"
    return RiskAssessment(score=min(1.0, score), level=level, components=components)


# ---------------------------------------------------------------------------
# 引擎
# ---------------------------------------------------------------------------


@dataclass
class RuleRunResult:
    results: list[RuleResult]
    risk: RiskAssessment

    @property
    def failed(self) -> list[RuleResult]:
        return [r for r in self.results if r.failed]

    @property
    def failed_names(self) -> list[str]:
        return [r.rule_name for r in self.failed]

    def as_prompt_view(self) -> list[dict[str, Any]]:
        """给 Agent 的失败项视图。"""
        return [
            {
                "rule_name": r.rule_name,
                "severity": r.severity.value,
                "message": r.message,
                "evidence_ids": r.evidence_ids,
            }
            for r in self.failed
        ]


class RuleEngine:
    """按固定顺序执行全部规则（规则执行不重试，文档 §21.1）。"""

    def __init__(self, rules: list[Rule] | None = None) -> None:
        self.rules = rules if rules is not None else default_rules()

    async def run(self, ctx: RuleContext) -> RuleRunResult:
        results: list[RuleResult] = []
        for rule in self.rules:
            try:
                results.append(await rule.evaluate(ctx))
            except Exception as exc:  # noqa: BLE001 - 单条规则异常不应中断整体校验
                results.append(
                    RuleResult(
                        rule_name=rule.name,
                        status=RuleStatus.FAILED,
                        severity=Severity.MEDIUM,
                        message=f"规则执行异常: {exc}",
                        rule_version=rule.version,
                    )
                )
        risk = assess_risk(ctx, results)
        return RuleRunResult(results=results, risk=risk)

    async def run_and_persist(
        self,
        session: AsyncSession,
        ctx: RuleContext,
        *,
        trace_id: str | None = None,
    ) -> RuleRunResult:
        """执行规则并把每条结果写入 checks 表与 Trace（文档 §9.3）。"""
        outcome = await self.run(ctx)
        for r in outcome.results:
            session.add(
                Check(
                    id=new_id("chk"),
                    case_id=ctx.case_id,
                    rule_name=r.rule_name,
                    rule_version=r.rule_version,
                    status=r.status.value,
                    severity=r.severity.value,
                    input_json=r.input_json or {},
                    result_json={
                        **r.result_json,
                        "message": r.message,
                        "evidence_ids": r.evidence_ids,
                    },
                )
            )
            await emit_trace(
                session,
                case_id=ctx.case_id,
                trace_id=trace_id,
                event_type=EVENT_RULE_EVALUATED,
                actor="rule_engine",
                payload={
                    "rule_name": r.rule_name,
                    "status": r.status.value,
                    "severity": r.severity.value,
                    "message": r.message,
                    "rule_version": r.rule_version,
                },
            )
        return outcome


def default_rules() -> list[Rule]:
    """默认规则集（文档 §9.1 全部 8 条）。"""
    from .rules.amount_matches_bank import AmountMatchesBank
    from .rules.amount_matches_receipt import AmountMatchesReceipt
    from .rules.approval_required import ApprovalRequired
    from .rules.category_limit import CategoryLimit
    from .rules.date_in_range import DateInRange
    from .rules.duplicate_invoice import DuplicateInvoice
    from .rules.invoice_required_fields import InvoiceRequiredFields
    from .rules.vendor_check import VendorCheck

    return [
        InvoiceRequiredFields(),
        AmountMatchesReceipt(),
        AmountMatchesBank(),
        DateInRange(),
        DuplicateInvoice(),
        CategoryLimit(),
        VendorCheck(),
        ApprovalRequired(),
    ]


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def parse_date(value: Any) -> date | None:
    """把多种日期写法解析为 date；失败返回 None（由规则判为 SKIPPED）。"""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()[:19].replace("年", "-").replace("月", "-").replace("日", "")
    text = text.replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y-%m", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").replace("¥", "").replace("￥", "").strip())
    except (ValueError, AttributeError):
        return None
