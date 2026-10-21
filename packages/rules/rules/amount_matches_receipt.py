"""规则：发票金额与收据金额一致（文档 §9.1 amount_matches_receipt）。"""

from __future__ import annotations

from ..engine import Rule, RuleContext, RuleResult, Severity, as_float

TOLERANCE = 0.01


class AmountMatchesReceipt(Rule):
    name = "amount_matches_receipt"
    description = "核对发票金额与收据金额是否一致"

    async def evaluate(self, ctx: RuleContext) -> RuleResult:
        receipt_fields = ctx.field_doc("receipt")
        if "receipt" not in ctx.document_types or not receipt_fields:
            return self.skipped("无收据文档，跳过金额核对")

        invoice_amount = as_float(ctx.field_doc("invoice").get("amount")) or as_float(ctx.fields.get("amount"))
        receipt_amount = as_float(receipt_fields.get("amount"))
        if invoice_amount is None or receipt_amount is None:
            return self.skipped("发票或收据金额无法解析", invoice_amount=invoice_amount, receipt_amount=receipt_amount)

        evidence = ctx.evidence_of("amount")
        # 先按分位取整再比较，避免 100.00 与 100.01 的浮点差值 0.010000000000005 被误判为超差
        if round(abs(invoice_amount - receipt_amount), 2) <= TOLERANCE:
            return self.passed(
                "发票与收据金额一致",
                evidence_ids=evidence,
                invoice_amount=invoice_amount,
                receipt_amount=receipt_amount,
            )
        return self.failed(
            f"发票金额 {invoice_amount:.2f} 与收据金额 {receipt_amount:.2f} 不一致",
            severity=Severity.HIGH,
            evidence_ids=evidence,
            invoice_amount=invoice_amount,
            receipt_amount=receipt_amount,
            diff=round(invoice_amount - receipt_amount, 2),
        )
