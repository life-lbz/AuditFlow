"""第二层验证：模型验证 + 两层合并（文档 §12.1 / §12.3）。

两层关系：
- 确定性层先跑，失败即否决（成本低、结论可靠）。
- 模型层在确定性层通过后再跑，负责语义层面的证据支撑与自相矛盾检查。
- 任一层失败都视为整体失败；Verifier 不能修改案件结论，只能否决并给出建议动作。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..gateway.base import ModelGateway, VerificationRequest, VerificationResult
from .deterministic import DeterministicContext, verify_deterministic


@dataclass
class TwoLayerVerifier:
    gateway: ModelGateway

    async def verify(
        self,
        *,
        decision: dict[str, Any],
        case: dict[str, Any],
        rule_results: list[dict[str, Any]] | None = None,
        candidate_evidence: list[dict[str, Any]] | None = None,
        policy_evidence: list[dict[str, Any]] | None = None,
        erp_snapshot: dict[str, Any] | None = None,
        erp_payload: dict[str, Any] | None = None,
    ) -> VerificationResult:
        ctx = DeterministicContext(
            decision=decision,
            case=case,
            rule_results=rule_results or [],
            candidate_evidence=candidate_evidence or [],
            policy_evidence=policy_evidence or [],
            erp_snapshot=erp_snapshot,
            erp_payload=erp_payload,
        )

        # 第一层：确定性
        deterministic = verify_deterministic(ctx)
        if deterministic.status == "failed":
            return deterministic

        # 第二层：模型（文档 §12.1 第二层）
        request = VerificationRequest(
            case_summary=case,
            decision=decision,
            evidence=[
                {
                    "evidence_id": e.get("evidence_id"),
                    "source_type": e.get("source_type"),
                    "quote": e.get("quote"),
                }
                for e in candidate_evidence or []
            ],
            failed_checks=[r for r in (rule_results or []) if r.get("status") == "failed"],
            policy_quotes=[
                {"source": e.get("section"), "quote": e.get("quote")} for e in (policy_evidence or [])
            ],
        )
        model_result = await self.gateway.verify(request)

        if model_result.status == "failed":
            # 合并两层失败项，便于归因
            merged = sorted({*deterministic.failed_checks, *model_result.failed_checks})
            return VerificationResult(
                status="failed",
                reason=model_result.reason,
                blocking=model_result.blocking,
                failed_checks=merged,
                suggested_action=model_result.suggested_action or "retry_agent_once",
                call=model_result.call,
            )
        return VerificationResult(
            status="passed",
            reason="确定性检查与语义验证均通过",
            blocking=False,
            failed_checks=[],
            suggested_action="none",
            call=model_result.call,
        )


async def verify_with_retry(
    verifier: TwoLayerVerifier,
    *,
    decision: dict[str, Any],
    retry_decision: Any | None = None,
    **kwargs: Any,
) -> tuple[VerificationResult, bool]:
    """验证 + 最多一次 Agent 重试（文档 §12.3）。

    返回 (最终验证结果, 是否发生过重试)。
    retry_decision 为一个 async 可调用对象：返回新的 decision 字典。
    """
    first = await verifier.verify(decision=decision, **kwargs)
    if first.status == "passed" or retry_decision is None:
        return first, False

    new_decision = await retry_decision()
    if not new_decision:
        return first, False
    second = await verifier.verify(decision=new_decision, **kwargs)
    return second, True
