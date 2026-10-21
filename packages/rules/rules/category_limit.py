"""规则：报销类型额度校验（文档 §9.1 category_limit）。

额度来源为制度条款解析结果（rules/limits.py），不依赖模型算数。
- travel         ：住宿每晚额度（可解析晚数时按均价，否则按单票金额）+ 差旅总额上限
- meal           ：人均额度
- office_supplies：单件额度
"""

from __future__ import annotations

from ..engine import Rule, RuleContext, RuleResult, Severity, as_float


class CategoryLimit(Rule):
    name = "category_limit"
    description = "按报销类型与制度额度校验是否超限"

    async def evaluate(self, ctx: RuleContext) -> RuleResult:
        invoice = ctx.field_doc("invoice")
        amount = as_float(invoice.get("amount")) or as_float(ctx.fields.get("amount")) or float(ctx.amount or 0)
        evidence = ctx.evidence_of("amount") + [e.evidence_id for e in ctx.policy_evidence]

        if ctx.category == "travel":
            return self._check_travel(ctx, amount, evidence)
        if ctx.category == "meal":
            return self._check_meal(ctx, amount, evidence)
        if ctx.category == "office_supplies":
            return self._check_office(ctx, amount, evidence)
        return self.skipped(f"未知报销类型 {ctx.category}，跳过额度校验", amount=amount)

    # ------------------------------------------------------------------
    def _check_travel(self, ctx: RuleContext, amount: float, evidence: list[str]) -> RuleResult:
        nightly = ctx.policy_limits.get("hotel_nightly")
        cap = ctx.policy_limits.get("category_cap")
        nights = as_float(ctx.field_doc("invoice").get("nights") or ctx.fields.get("nights"))

        if nightly is None and cap is None:
            return self.skipped("未检索到差旅额度条款，无法判断是否超限", amount=amount)

        # 单票/每晚额度
        if nightly is not None:
            effective_nights = nights if nights and nights > 0 else 1.0
            per_night = amount / effective_nights
            if per_night > nightly + 1e-6:
                return self.failed(
                    f"住宿费用超出制度额度：每晚 {per_night:.2f} 元 > 上限 {nightly:.2f} 元"
                    f"（总额 {amount:.2f} 元 / {effective_nights:g} 晚）",
                    severity=Severity.MEDIUM,
                    evidence_ids=evidence,
                    per_night=round(per_night, 2),
                    limit=nightly,
                    nights=effective_nights,
                )

        # 差旅总额上限
        if cap is not None and amount > cap + 1e-6:
            return self.failed(
                f"差旅住宿总额 {amount:.2f} 元超出单次上限 {cap:.2f} 元",
                severity=Severity.MEDIUM,
                evidence_ids=evidence,
                amount=amount,
                limit=cap,
            )

        return self.passed(
            "住宿费用未超制度额度",
            evidence_ids=evidence,
            amount=amount,
            nightly_limit=nightly,
            nights=nights,
        )

    def _check_meal(self, ctx: RuleContext, amount: float, evidence: list[str]) -> RuleResult:
        per_person = ctx.policy_limits.get("meal_per_person")
        if per_person is None:
            return self.skipped("未检索到餐饮额度条款，无法判断是否超限", amount=amount)

        attendees = as_float(ctx.field_doc("invoice").get("attendees") or ctx.fields.get("attendees")) or 1.0
        avg = amount / max(1.0, attendees)
        if avg > per_person + 1e-6:
            return self.failed(
                f"餐饮人均费用 {avg:.2f} 元超出制度上限 {per_person:.2f} 元（总额 {amount:.2f} 元 / {attendees:g} 人）",
                severity=Severity.MEDIUM,
                evidence_ids=evidence,
                per_person=round(avg, 2),
                limit=per_person,
                attendees=attendees,
            )
        return self.passed("餐饮人均费用未超制度额度", evidence_ids=evidence, per_person=round(avg, 2), limit=per_person)

    def _check_office(self, ctx: RuleContext, amount: float, evidence: list[str]) -> RuleResult:
        single = ctx.policy_limits.get("office_single_item")
        if single is None:
            return self.skipped("未检索到办公用品额度条款，无法判断是否超限", amount=amount)
        if amount > single + 1e-6:
            return self.failed(
                f"办公用品单件金额 {amount:.2f} 元超出制度上限 {single:.2f} 元",
                severity=Severity.MEDIUM,
                evidence_ids=evidence,
                amount=amount,
                limit=single,
            )
        return self.passed("办公用品单件金额未超制度额度", evidence_ids=evidence, amount=amount, limit=single)
