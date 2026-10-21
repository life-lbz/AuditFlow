"""第一层验证：确定性检查（文档 §12.1）。

不依赖模型，全部由代码判定：
- 结论引用的证据是否真实存在
- approve 是否有制度引用与规则支撑
- 金额/日期/重复检查是否与规则结果一致
- ERP 返回是否与提交参数一致
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..gateway.base import VerificationResult


@dataclass
class DeterministicContext:
    """确定性验证所需的输入。"""

    decision: dict[str, Any] = field(default_factory=dict)
    case: dict[str, Any] = field(default_factory=dict)
    rule_results: list[dict[str, Any]] = field(default_factory=list)
    candidate_evidence: list[dict[str, Any]] = field(default_factory=list)
    policy_evidence: list[dict[str, Any]] = field(default_factory=list)
    erp_snapshot: dict[str, Any] | None = None
    erp_payload: dict[str, Any] | None = None


def verify_deterministic(ctx: DeterministicContext) -> VerificationResult:
    failed_checks: list[str] = []
    reasons: list[str] = []

    decision = ctx.decision or {}
    action = decision.get("decision")
    evidence_ids = [str(e) for e in (decision.get("evidence_ids") or [])]

    # 1) 证据引用必须真实存在（文档 §12.1：证据引用是否存在）
    known_ids = {str(e.get("evidence_id")) for e in ctx.candidate_evidence if e.get("evidence_id")}
    unknown = [e for e in evidence_ids if e not in known_ids]
    if unknown:
        failed_checks.append("evidence_not_found")
        reasons.append(f"结论引用了不存在的证据：{', '.join(unknown[:3])}")

    # 2) 结论必须至少引用一条证据
    if not evidence_ids:
        failed_checks.append("evidence_coverage")
        reasons.append("结论未引用任何证据")

    # 3) approve 必须有制度条款支撑
    if action == "approve":
        if not ctx.policy_evidence:
            failed_checks.append("policy_support")
            reasons.append("approve 结论缺少制度条款引用")
        blocking_rules = [
            r for r in ctx.rule_results
            if r.get("status") == "failed" and r.get("severity") in {"high", "medium"}
        ]
        if blocking_rules:
            names = "、".join(r.get("rule_name", "") for r in blocking_rules[:3])
            failed_checks.append("rule_conflict")
            reasons.append(f"存在未解决的规则失败项（{names}），不应 approve")

    # 4) approve 与风险等级不得矛盾
    if action == "approve" and decision.get("risk_level") == "high":
        failed_checks.append("self_contradiction")
        reasons.append("approve 结论与 high 风险等级矛盾")

    # 5) need_info 必须说明缺少什么
    if action == "need_info" and not decision.get("missing_items"):
        failed_checks.append("missing_items_unspecified")
        reasons.append("need_info 结论未说明缺少的材料")

    # 6) reject 必须有依据
    if action == "reject" and not (decision.get("failed_checks") or decision.get("summary")):
        failed_checks.append("reject_without_reason")
        reasons.append("reject 结论缺少失败依据")

    # 7) ERP 回执与提交参数一致性（提交后验证，文档 §12.1）
    if ctx.erp_snapshot and ctx.erp_payload:
        mismatches: list[str] = []
        snapshot = ctx.erp_snapshot
        if snapshot.get("case_id") and snapshot["case_id"] != ctx.erp_payload.get("case_id"):
            mismatches.append("case_id")
        try:
            if snapshot.get("amount") is not None and abs(
                float(snapshot["amount"]) - float(ctx.erp_payload.get("amount", 0))
            ) > 0.01:
                mismatches.append("amount")
        except (TypeError, ValueError):
            mismatches.append("amount")
        if mismatches:
            failed_checks.append("erp_mismatch")
            reasons.append(f"ERP 返回与提交参数不一致：{', '.join(mismatches)}")

    if failed_checks:
        return VerificationResult(
            status="failed",
            reason="；".join(reasons),
            blocking=True,
            failed_checks=failed_checks,
            suggested_action="retry_agent_once",
        )
    return VerificationResult.passed_result("确定性检查全部通过")
