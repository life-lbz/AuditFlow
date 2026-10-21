"""高风险写工具：提交报销单（文档 §11.3 七条前置条件）。

该工具不直接访问 ERP，全部经由 Action Gateway：
Verifier 通过 → 批准校验 → payload 校验 → 幂等 → preview → submit → 查询验证。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..registry import BaseTool, ToolContext, ToolError, ToolPermission, ToolSpec
from ...erp.gateway import ActionGateway


class SubmitExpenseArgs(BaseModel):
    vendor: str = Field(default="", description="供应商名称")
    description: str = Field(default="", description="报销事由")
    confirm: bool = Field(default=True, description="确认执行提交（幂等，重复提交不会产生新单据）")


class SubmitExpense(BaseTool):
    spec = ToolSpec(
        name="submit_expense",
        description="经 Action Gateway 把报销单提交到 ERP（高风险写操作，需 Verifier 通过且已获批准）",
        permission=ToolPermission.HIGH_RISK_WRITE,
        timeout_ms=30_000,
        max_retries=0,
        idempotent=True,
    )
    args_model = SubmitExpenseArgs

    async def run(self, args: SubmitExpenseArgs, ctx: ToolContext) -> dict:
        if not args.confirm:
            raise ToolError("未确认提交", error_code="not_confirmed")

        gateway = ActionGateway(ctx.session)
        payload = gateway.build_payload(ctx.case, vendor=args.vendor, description=args.description)
        outcome = await gateway.submit(
            ctx.case,
            payload,
            approved_by=ctx.approved_by,
            verifier_passed=ctx.verifier_passed,
            trace_id=ctx.trace_id,
        )
        return outcome.as_dict()
