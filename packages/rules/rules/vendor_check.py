"""规则：供应商风险校验（文档 §9.1 vendor_check）。"""

from __future__ import annotations

from ..engine import Rule, RuleContext, RuleResult, Severity


class VendorCheck(Rule):
    name = "vendor_check"
    description = "检查供应商是否命中黑名单或异常名单"

    async def evaluate(self, ctx: RuleContext) -> RuleResult:
        invoice = ctx.field_doc("invoice")
        vendor = (invoice.get("vendor") or ctx.fields.get("vendor") or "").strip()
        if not vendor:
            return self.skipped("未抽取到供应商名称，跳过供应商校验")

        evidence = ctx.evidence_of("vendor")

        for bad in ctx.vendor_blacklist:
            if bad and bad in vendor:
                return self.failed(
                    f"供应商「{vendor}」命中风险名单：{bad}",
                    severity=Severity.HIGH,
                    evidence_ids=evidence,
                    vendor=vendor,
                    matched_rule=bad,
                )

        for good in ctx.vendor_whitelist:
            if good and good in vendor:
                return self.passed(
                    f"供应商「{vendor}」在白名单内",
                    evidence_ids=evidence,
                    vendor=vendor,
                    matched_rule=good,
                )

        # 未命中任何名单：不阻断，但记录为需关注项（由风险评分体现）
        return self.passed(
            f"供应商「{vendor}」未命中风险名单（非白名单，建议关注）",
            evidence_ids=evidence,
            vendor=vendor,
            in_whitelist=False,
        )
