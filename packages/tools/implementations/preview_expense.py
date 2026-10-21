"""可撤销写工具：预览报销单（文档 §11.1 preview_expense）。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..registry import BaseTool, ToolContext, ToolPermission, ToolSpec
from ...erp.gateway import ActionGateway


class PreviewExpenseArgs(BaseModel):
    vendor: str = Field(default="", description="供应商名称")
    description: str = Field(default="", description="报销事由")


class PreviewExpense(BaseTool):
    spec = ToolSpec(
        name="preview_expense",
        description="预览将要提交到 ERP 的报销单内容与幂等键，不产生任何副作用",
        permission=ToolPermission.WRITE,
        timeout_ms=5_000,
        max_retries=0,
        idempotent=True,
    )
    args_model = PreviewExpenseArgs

    async def run(self, args: PreviewExpenseArgs, ctx: ToolContext) -> dict:
        gateway = ActionGateway(ctx.session)
        payload = gateway.build_payload(ctx.case, vendor=args.vendor, description=args.description)
        preview = gateway.preview(ctx.case, payload)
        return {**preview, "case_status": ctx.case.status, "risk_level": ctx.case.risk_level}
