"""只读工具：查询 ERP 单据状态（文档 §11.1 get_erp_record）。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..registry import BaseTool, ToolContext, ToolError, ToolPermission, ToolSpec
from ...erp.client import ErpClient, ErpClientError


class GetErpArgs(BaseModel):
    erp_id: str | None = Field(default=None, description="ERP 单据号；省略则查询本案件最近一次事务")
    include_history: bool = Field(default=False, description="是否同时返回单据操作历史")


class GetErpRecord(BaseTool):
    spec = ToolSpec(
        name="get_erp_record",
        description="查询 ERP 报销单的真实状态与字段，用于提交后确认或超时后核查",
        permission=ToolPermission.READ_ONLY,
        timeout_ms=10_000,
        max_retries=2,
        idempotent=True,
    )
    args_model = GetErpArgs

    async def run(self, args: GetErpArgs, ctx: ToolContext) -> dict:
        erp_id = args.erp_id
        if erp_id is None:
            from sqlalchemy import select

            from ...core.models import ErpTransaction

            txn = (
                await ctx.session.execute(
                    select(ErpTransaction)
                    .where(ErpTransaction.case_id == ctx.case_id)
                    .order_by(ErpTransaction.created_at.desc())
                )
            ).scalars().first()
            if txn is None or not txn.erp_id:
                return {
                    "found": False,
                    "reason": "no_erp_transaction",
                    "message": "案件尚无 ERP 提交记录",
                }
            erp_id = txn.erp_id

        client = ErpClient()
        try:
            record = await client.get_expense(erp_id)
        except ErpClientError as exc:
            if exc.status_code == 404:
                return {"found": False, "erp_id": erp_id, "message": "ERP 中不存在该单据"}
            raise ToolError(f"ERP 查询失败: {exc}", error_code="erp_query_failed") from exc

        data = {"found": True, "erp_id": erp_id, "record": record}
        if args.include_history:
            try:
                data["history"] = await client.get_history(erp_id)
            except ErpClientError:
                data["history"] = None
        return data
