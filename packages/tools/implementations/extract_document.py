"""只读工具：重新解析指定文档（文档 §11.1 extract_document）。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..registry import BaseTool, ToolContext, ToolError, ToolPermission, ToolSpec
from ...core.models import Document
from ...documents.extractor import extract_document as do_extract
from ...documents.parser import DocumentParseError, parse_bytes
from ...core.storage import get_storage


class ExtractArgs(BaseModel):
    document_id: str = Field(description="要重新解析的文档 ID")
    force: bool = Field(default=True, description="是否强制重新抽取（忽略缓存）")


class ExtractDocument(BaseTool):
    spec = ToolSpec(
        name="extract_document",
        description="重新解析指定文档并刷新字段与证据（用于低置信度字段的二次识别）",
        permission=ToolPermission.READ_ONLY,
        timeout_ms=60_000,
        max_retries=1,
        idempotent=True,
    )
    args_model = ExtractArgs

    async def run(self, args: ExtractArgs, ctx: ToolContext) -> dict:
        document = await ctx.session.get(Document, args.document_id)
        if document is None:
            raise ToolError(f"文档不存在: {args.document_id}", error_code="document_not_found")
        if document.case_id != ctx.case_id:
            raise ToolError("文档不属于当前案件", error_code="cross_case_access")

        storage = get_storage()
        try:
            data = await storage.get(document.storage_key)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"读取文档失败: {exc}", error_code="storage_error") from exc

        try:
            parsed = parse_bytes(data, document.storage_key.split("/")[-1])
        except DocumentParseError as exc:
            raise ToolError(str(exc), error_code="parse_error") from exc

        # 重新抽取前清除旧字段，避免叠加
        from sqlalchemy import delete

        from ...core.models import ExtractedField

        await ctx.session.execute(delete(ExtractedField).where(ExtractedField.document_id == document.id))

        result = await do_extract(
            ctx.session,
            ctx.gateway,
            case_id=ctx.case_id,
            document=document,
            parsed=parsed,
            trace_id=ctx.trace_id,
        )
        return {
            "document_id": document.id,
            "document_type": document.type,
            "fields": {k: {"value": v.value, "confidence": v.confidence} for k, v in result.fields.items()},
            "min_confidence": result.min_confidence(),
            "degraded": result.degraded,
        }
