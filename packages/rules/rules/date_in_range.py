"""规则：票据日期在出差日期范围内（文档 §9.1 date_in_range）。"""

from __future__ import annotations

from datetime import timedelta

from ..engine import Rule, RuleContext, RuleResult, Severity, parse_date

# 允许行程前后各 1 天的合理浮动（交通、住宿发生在行程边界属正常情形）
ALLOWED_SLACK_DAYS = 1


class DateInRange(Rule):
    name = "date_in_range"
    description = "核对票据日期是否落在出差起止日期范围内"

    async def evaluate(self, ctx: RuleContext) -> RuleResult:
        ticket_date = parse_date(ctx.field_doc("invoice").get("date")) or parse_date(ctx.fields.get("date"))
        if ticket_date is None:
            return self.skipped("票据日期无法解析，跳过日期范围校验")

        trip_start, trip_end = self._trip_range(ctx)
        if trip_start is None or trip_end is None:
            return self.skipped("缺少出差起止日期（无审批邮件或事由无法解析）", ticket_date=str(ticket_date))

        low = trip_start - timedelta(days=ALLOWED_SLACK_DAYS)
        high = trip_end + timedelta(days=ALLOWED_SLACK_DAYS)
        evidence = ctx.evidence_of("date")
        if low <= ticket_date <= high:
            return self.passed(
                f"票据日期 {ticket_date} 在行程 {trip_start}~{trip_end} 范围内",
                evidence_ids=evidence,
                ticket_date=str(ticket_date),
                trip_start=str(trip_start),
                trip_end=str(trip_end),
            )
        return self.failed(
            f"票据日期 {ticket_date} 不在出差行程 {trip_start}~{trip_end} 范围内",
            severity=Severity.MEDIUM,
            evidence_ids=evidence,
            ticket_date=str(ticket_date),
            trip_start=str(trip_start),
            trip_end=str(trip_end),
        )

    def _trip_range(self, ctx: RuleContext):
        approval = ctx.field_doc("approval_email")
        start = parse_date(approval.get("trip_start")) or parse_date(ctx.fields.get("trip_start"))
        end = parse_date(approval.get("trip_end")) or parse_date(ctx.fields.get("trip_end"))
        if start and end:
            return start, end
        # 退化：只有一个行程日期时，视为当日行程
        single = start or end
        if single:
            return single, single
        return None, None
