"""规则：重复报销检测（文档 §9.1 duplicate_invoice）。

判定依据：同一发票号在历史案件中出现，或「金额 + 日期 + 供应商」完全一致。
"""

from __future__ import annotations

from typing import Any

from ..engine import Rule, RuleContext, RuleResult, Severity, as_float, parse_date


class DuplicateInvoice(Rule):
    name = "duplicate_invoice"
    description = "检查发票号或金额/日期/供应商组合是否与历史案件重复"

    async def evaluate(self, ctx: RuleContext) -> RuleResult:
        invoice = ctx.field_doc("invoice")
        invoice_no = invoice.get("invoice_no") or ctx.fields.get("invoice_no")
        amount = as_float(invoice.get("amount")) or as_float(ctx.fields.get("amount"))
        date = parse_date(invoice.get("date") or ctx.fields.get("date"))
        vendor = invoice.get("vendor") or ctx.fields.get("vendor")

        if not invoice_no and not (amount and date and vendor):
            return self.skipped("缺少发票号或金额/日期/供应商组合，无法做重复判断")

        hits: list[dict[str, Any]] = []
        for past in ctx.history:
            if past.get("case_id") == ctx.case_id:
                continue
            if invoice_no and str(past.get("invoice_no") or "") == str(invoice_no):
                hits.append({**past, "match": "invoice_no"})
                continue
            same_amount = amount is not None and as_float(past.get("amount")) == amount
            same_date = date is not None and parse_date(past.get("date")) == date
            same_vendor = bool(vendor) and str(past.get("vendor") or "") == str(vendor)
            if same_amount and same_date and same_vendor:
                hits.append({**past, "match": "amount+date+vendor"})

        history_evidence = [h.get("evidence_id") for h in hits if h.get("evidence_id")]
        if hits:
            first = hits[0]
            return self.failed(
                f"疑似重复报销：发票号 {invoice_no or '(缺失)'} 与历史案件 {first.get('case_id')} "
                f"重复（匹配维度：{first.get('match')}）",
                severity=Severity.HIGH,
                evidence_ids=ctx.evidence_of("invoice_no") + history_evidence,
                duplicate_of=[h.get("case_id") for h in hits],
                match_dimensions=[h.get("match") for h in hits],
            )

        return self.passed(
            "未发现重复报销",
            evidence_ids=ctx.evidence_of("invoice_no"),
            checked_history=len(ctx.history),
        )
