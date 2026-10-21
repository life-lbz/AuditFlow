"""规则：发票金额与银行流水金额一致（文档 §9.1 amount_matches_bank）。"""

from __future__ import annotations

from ..engine import Rule, RuleContext, RuleResult, Severity, as_float

TOLERANCE = 0.01


class AmountMatchesBank(Rule):
    name = "amount_matches_bank"
    description = "核对发票金额与银行流水金额是否一致"

    async def evaluate(self, ctx: RuleContext) -> RuleResult:
        bank_fields = ctx.field_doc("bank_statement")
        if "bank_statement" not in ctx.document_types or not bank_fields:
            return self.skipped("无银行流水文档，跳过流水核对")

        invoice_amount = as_float(ctx.field_doc("invoice").get("amount")) or as_float(ctx.fields.get("amount"))
        bank_amount = as_float(bank_fields.get("amount"))
        if invoice_amount is None or bank_amount is None:
            return self.skipped("发票或流水金额无法解析", invoice_amount=invoice_amount, bank_amount=bank_amount)

        evidence = ctx.evidence_of("amount")
        # 先按分位取整再比较，避免 100.00 与 100.01 的浮点差值 0.010000000000005 被误判为超差
        if round(abs(invoice_amount - bank_amount), 2) <= TOLERANCE:
            return self.passed(
                "发票与流水金额一致",
                evidence_ids=evidence,
                invoice_amount=invoice_amount,
                bank_amount=bank_amount,
            )
        return self.failed(
            f"发票金额 {invoice_amount:.2f} 与流水金额 {bank_amount:.2f} 不一致",
            severity=Severity.HIGH,
            evidence_ids=evidence,
            invoice_amount=invoice_amount,
            bank_amount=bank_amount,
            diff=round(invoice_amount - bank_amount, 2),
        )
