"""只读工具：匹配银行流水（文档 §11.1 match_bank_transaction）。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..registry import BaseTool, ToolContext, ToolSpec, ToolPermission
from ...core.models import Document
from ...documents.extractor import load_case_fields
from ...rules.engine import as_float


class MatchBankArgs(BaseModel):
    amount: float | None = Field(default=None, description="待匹配金额；省略则使用案件申报金额")
    tolerance: float = Field(default=0.01, ge=0.0, le=1.0, description="允许的金额误差")


class MatchBankTransaction(BaseTool):
    spec = ToolSpec(
        name="match_bank_transaction",
        description="在案件银行流水截图中按金额匹配交易记录，用于核对票据与流水是否一致",
        permission=ToolPermission.READ_ONLY,
        timeout_ms=10_000,
        max_retries=2,
        idempotent=True,
    )
    args_model = MatchBankArgs

    async def run(self, args: MatchBankArgs, ctx: ToolContext) -> dict:
        from sqlalchemy import select

        bank_docs = (
            await ctx.session.execute(
                select(Document).where(
                    Document.case_id == ctx.case_id,
                    Document.type == "bank_statement",
                )
            )
        ).scalars().all()

        target = args.amount if args.amount is not None else float(ctx.case.amount or 0)

        if not bank_docs:
            return {
                "matched": False,
                "reason": "missing_bank_statement",
                "message": "案件中没有银行流水文档，无法完成金额核对",
                "target_amount": target,
            }

        cf = await load_case_fields(ctx.session, ctx.case_id)
        candidates: list[dict] = []
        matched: dict | None = None
        for doc in bank_docs:
            fields = cf.by_document.get(doc.id, {})
            amount = as_float(fields.get("amount"))
            entry = {
                "document_id": doc.id,
                "amount": amount,
                "transaction_time": fields.get("transaction_time"),
                "counterparty": fields.get("counterparty"),
                "transaction_no": fields.get("transaction_no"),
            }
            candidates.append(entry)
            if amount is not None and abs(amount - target) <= args.tolerance:
                matched = entry

        return {
            "matched": matched is not None,
            "target_amount": target,
            "matched_entry": matched,
            "candidates": candidates,
            "diff": round(target - matched["amount"], 2) if matched and matched["amount"] is not None else None,
        }
