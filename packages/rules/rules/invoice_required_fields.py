"""规则：发票必需字段完整性（文档 §9.1 invoice_required_fields）。"""

from __future__ import annotations

from ..engine import Rule, RuleContext, RuleResult, Severity
from ..schema_fields import FIELD_LABELS, INVOICE_REQUIRED


class InvoiceRequiredFields(Rule):
    name = "invoice_required_fields"
    description = "检查发票是否包含发票号、金额、日期、供应商等必需字段"

    async def evaluate(self, ctx: RuleContext) -> RuleResult:
        if "invoice" not in ctx.document_types:
            return self.failed(
                "案件缺少发票文档，无法核验报销依据",
                severity=Severity.HIGH,
                has_invoice=False,
            )

        invoice_fields = ctx.field_doc("invoice")
        missing = [f for f in INVOICE_REQUIRED if not invoice_fields.get(f)]
        evidence = [e for f in INVOICE_REQUIRED for e in ctx.evidence_of(f)]

        if missing:
            labels = "、".join(FIELD_LABELS.get(f, f) for f in missing)
            return self.failed(
                f"发票缺少必需字段：{labels}",
                severity=Severity.HIGH,
                evidence_ids=evidence,
                missing_fields=missing,
                invoice_document_id=ctx.document_ids.get("invoice"),
            )

        # 金额必须为正数且可解析
        from ..engine import as_float

        amount = as_float(invoice_fields.get("amount"))
        if amount is None or amount <= 0:
            return self.failed(
                "发票金额缺失或非法",
                severity=Severity.HIGH,
                evidence_ids=evidence,
                missing_fields=["amount"],
            )

        return self.passed(
            "发票必需字段完整",
            evidence_ids=evidence,
            fields={k: invoice_fields.get(k) for k in INVOICE_REQUIRED},
            amount=amount,
        )
