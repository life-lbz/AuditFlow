"""只读工具：检查重复报销（文档 §11.1 check_duplicate）。"""

from __future__ import annotations

from pydantic import BaseModel, Field
from sqlalchemy import select

from ..registry import BaseTool, ToolContext, ToolPermission, ToolSpec
from ...core.models import Case, CaseStatus, Document, ExtractedField
from ...rules.engine import as_float


class CheckDuplicateArgs(BaseModel):
    invoice_no: str | None = Field(default=None, description="发票号；省略则使用案件已抽取的发票号")
    amount: float | None = Field(default=None, description="金额；用于组合维度比对")


class CheckDuplicate(BaseTool):
    spec = ToolSpec(
        name="check_duplicate",
        description="检查发票号（或金额+日期+供应商组合）是否已在历史案件中出现，避免重复报销",
        permission=ToolPermission.READ_ONLY,
        timeout_ms=10_000,
        max_retries=2,
        idempotent=True,
    )
    args_model = CheckDuplicateArgs

    async def run(self, args: CheckDuplicateArgs, ctx: ToolContext) -> dict:
        invoice_no = args.invoice_no or await self._case_field(ctx, "invoice_no")
        amount = args.amount if args.amount is not None else float(ctx.case.amount or 0)

        rows = (
            await ctx.session.execute(
                select(ExtractedField, Document, Case)
                .join(Document, ExtractedField.document_id == Document.id)
                .join(Case, Document.case_id == Case.id)
                .where(
                    Document.type == "invoice",
                    Case.id != ctx.case_id,
                    Case.status.in_(
                        [
                            CaseStatus.COMPLETED.value,
                            CaseStatus.WAITING_APPROVAL.value,
                            CaseStatus.ESCALATED.value,
                            CaseStatus.VERIFIED.value,
                            CaseStatus.EXECUTING.value,
                        ]
                    ),
                )
            )
        ).all()

        by_case: dict[str, dict] = {}
        for field, doc, case in rows:
            entry = by_case.setdefault(case.id, {"case_id": case.id, "status": case.status, "fields": {}})
            entry["fields"][field.field_name] = field.value

        hits: list[dict] = []
        for case_id, entry in by_case.items():
            fields = entry["fields"]
            same_invoice = bool(invoice_no) and str(fields.get("invoice_no") or "") == str(invoice_no)
            same_amount = as_float(fields.get("amount")) == amount and amount > 0
            if same_invoice:
                hits.append({**entry, "match": "invoice_no"})
            elif same_amount and fields.get("date") and fields.get("vendor"):
                hits.append({**entry, "match": "amount(+date/vendor)"})

        return {
            "duplicate": bool(hits),
            "invoice_no": invoice_no,
            "amount": amount,
            "hits": hits,
            "checked_cases": len(by_case),
            "message": f"发现 {len(hits)} 个可疑历史案件" if hits else "未发现重复报销",
        }

    async def _case_field(self, ctx: ToolContext, name: str) -> str | None:
        rows = (
            await ctx.session.execute(
                select(ExtractedField)
                .join(Document, ExtractedField.document_id == Document.id)
                .where(Document.case_id == ctx.case_id, ExtractedField.field_name == name)
            )
        ).scalars().all()
        return rows[0].value if rows else None
