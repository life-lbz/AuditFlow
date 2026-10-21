"""规则：审批要求校验（文档 §9.1 approval_required）。"""

from __future__ import annotations

from ..engine import Rule, RuleContext, RuleResult, Severity, as_float

_APPROVED_KEYWORDS = ("通过", "同意", "批准", "已审批", "approved")
_REJECTED_KEYWORDS = ("驳回", "不同意", "拒绝", "rejected")


class ApprovalRequired(Rule):
    name = "approval_required"
    description = "检查超额或差旅案件是否具备有效审批"

    async def evaluate(self, ctx: RuleContext) -> RuleResult:
        invoice = ctx.field_doc("invoice")
        amount = as_float(invoice.get("amount")) or as_float(ctx.fields.get("amount")) or float(ctx.amount or 0)
        threshold = ctx.policy_limits.get("approval_threshold")
        approval = ctx.field_doc("approval_email")
        has_email = "approval_email" in ctx.document_types and bool(approval)

        # 差旅类案件无论金额都要求审批邮件（文档 §2.1 差旅关键检查）
        needs_approval = ctx.category == "travel" or (threshold is not None and amount > threshold)

        if not needs_approval:
            return self.passed(
                f"金额 {amount:.2f} 元未触发审批阈值"
                + (f"（阈值 {threshold:.2f} 元）" if threshold is not None else ""),
                amount=amount,
                threshold=threshold,
                approval_required=False,
            )

        if not has_email:
            reason = (
                "差旅报销必须提供出差审批邮件"
                if ctx.category == "travel"
                else f"金额 {amount:.2f} 元达到审批阈值 {threshold:.2f} 元，需提供审批记录"
            )
            return self.failed(
                reason,
                severity=Severity.HIGH,
                evidence_ids=ctx.evidence_of("approval_result"),
                amount=amount,
                threshold=threshold,
                approval_required=True,
                missing=["approval_email"],
            )

        result_text = str(approval.get("approval_result") or "").lower()
        evidence = ctx.evidence_of("approval_result") + ctx.evidence_of("approver")

        if any(k in result_text for k in _REJECTED_KEYWORDS):
            return self.failed(
                f"审批结果为否决（{approval.get('approval_result')}），不应继续报销",
                severity=Severity.HIGH,
                evidence_ids=evidence,
                approval_result=approval.get("approval_result"),
            )

        if any(k in result_text for k in _APPROVED_KEYWORDS):
            return self.passed(
                f"审批有效：审批人 {approval.get('approver', '(未知)')}，结果 {approval.get('approval_result')}",
                evidence_ids=evidence,
                approver=approval.get("approver"),
                approval_result=approval.get("approval_result"),
            )

        return self.failed(
            f"审批结果不明确（{approval.get('approval_result') or '空'}），无法确认已获批准",
            severity=Severity.MEDIUM,
            evidence_ids=evidence,
            approval_result=approval.get("approval_result"),
        )
