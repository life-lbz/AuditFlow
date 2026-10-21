"""字段抽取（文档 §7.3 / §7.4）。

抽取由 ModelGateway 完成；本模块负责：
1. 组装抽取请求（页面文本 + 文档类型 + 提示 schema）
2. 调用网关
3. 落库 ExtractedField（含 confidence / page_no / bbox）
4. 汇总案件级字段视图与最低置信度
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..gateway.base import (
    ExtractionRequest,
    ExtractionResult,
    ModelGateway,
)
from ..core.models import Document, ExtractedField, ExtractedField as _EF  # noqa: F401
from ..core.tracing import EVENT_DOCUMENT_EXTRACTED, emit_trace, new_id
from .schema import REQUIRED_FIELDS
from .parser import ParsedDocument


@dataclass
class CaseFields:
    """案件级字段汇总视图。"""

    values: dict[str, Any] = field(default_factory=dict)
    confidences: dict[str, float] = field(default_factory=dict)
    by_document: dict[str, dict[str, Any]] = field(default_factory=dict)
    low_confidence_documents: list[str] = field(default_factory=list)

    @property
    def min_confidence(self) -> float:
        return min(self.confidences.values()) if self.confidences else 1.0

    def as_prompt_view(self) -> dict[str, dict[str, Any]]:
        return {
            name: {"value": self.values[name], "confidence": self.confidences.get(name, 0.0)}
            for name in self.values
        }


async def extract_document(
    session: AsyncSession,
    gateway: ModelGateway,
    *,
    case_id: str,
    document: Document,
    parsed: ParsedDocument,
    trace_id: str | None = None,
    persist: bool = True,
) -> ExtractionResult:
    """对单个文档执行结构化抽取并落库。"""
    hint = {f: f for f in REQUIRED_FIELDS.get(_doc_type(document.type), ())}
    request = ExtractionRequest(
        document_type=_doc_type(document.type),
        pages=parsed.pages,
        image_keys=None,
        hint_schema=hint,
    )
    result = await gateway.extract_structured(request)

    if persist:
        for name, ef in result.fields.items():
            session.add(
                ExtractedField(
                    id=new_id("fld"),
                    document_id=document.id,
                    field_name=name,
                    value=_stringify(ef.value),
                    confidence=float(ef.confidence),
                    page_no=ef.page_no,
                    bbox=ef.bbox,
                )
            )

    await emit_trace(
        session,
        case_id=case_id,
        trace_id=trace_id,
        event_type=EVENT_DOCUMENT_EXTRACTED,
        actor="document_service",
        payload={
            "document_id": document.id,
            "document_type": document.type,
            "fields": {k: {"value": v.value, "confidence": v.confidence} for k, v in result.fields.items()},
            "degraded": result.degraded,
            "error": result.error,
        },
        model_record=result.call,
    )
    return result


async def load_case_fields(session: AsyncSession, case_id: str) -> CaseFields:
    """汇总案件下所有文档的抽取字段。

    同一字段在多个文档出现时（如发票金额与流水金额），以第一个出现的值为准，
    并记录每个文档各自的字段集，供规则引擎做交叉比对。
    """
    docs = (
        await session.execute(select(Document).where(Document.case_id == case_id))
    ).scalars().all()

    cf = CaseFields()
    for doc in docs:
        rows = (
            await session.execute(
                select(ExtractedField).where(ExtractedField.document_id == doc.id)
            )
        ).scalars().all()
        per_doc: dict[str, Any] = {}
        for row in rows:
            per_doc[row.field_name] = _parse_value(row.value)
            cf.confidences.setdefault(row.field_name, row.confidence)
            if row.field_name not in cf.values:
                cf.values[row.field_name] = _parse_value(row.value)
        cf.by_document[doc.id] = per_doc
        if per_doc and min(
            (r.confidence for r in rows), default=1.0
        ) < 0.7:
            cf.low_confidence_documents.append(doc.id)
    return cf


def _doc_type(raw: str):
    from ..gateway.base import DocumentType

    try:
        return DocumentType(raw)
    except ValueError:
        return DocumentType.UNKNOWN


def _stringify(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _parse_value(raw: str) -> Any:
    """把库中字符串还原为数字/字符串（金额与日期需要按类型参与规则计算）。"""
    if raw is None:
        return None
    text = str(raw).strip()
    try:
        if text.replace(".", "", 1).replace("-", "", 1).isdigit() and "." in text:
            return float(text)
        if text.isdigit():
            return int(text)
    except (ValueError, AttributeError):
        pass
    return text
