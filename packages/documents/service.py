"""文档服务：上传 → 存储 → 解析 → OCR → 抽取 的完整链路（文档 §7.1）。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.models import Document, DocumentPage
from ..core.storage import get_storage, sha256_bytes
from ..core.tracing import EVENT_DOCUMENT_UPLOADED, emit_trace, new_id
from ..gateway.base import ExtractionResult, ModelGateway
from . import parser as parser_mod
from .extractor import extract_document
from .ocr import get_ocr_backend
from .schema import missing_fields, detect_document_type


@dataclass
class IngestResult:
    document: Document
    parsed: parser_mod.ParsedDocument
    extraction: ExtractionResult | None = None
    ocr_backend: str = ""
    ocr_degraded: bool = False
    missing_fields: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


async def ingest_document(
    session: AsyncSession,
    gateway: ModelGateway,
    *,
    case_id: str,
    filename: str,
    data: bytes,
    declared_type: str | None = None,
    sidecar_text: str | None = None,
    trace_id: str | None = None,
) -> IngestResult:
    """接入一份文档并完成抽取。

    sidecar_text：纯图片 OCR 的替身文本（合成数据提供）；接入真实 OCR 后不再需要。
    """
    digest = sha256_bytes(data)

    # 1. 解析（CPU 密集，放线程池）
    parsed = await asyncio.to_thread(parser_mod.parse_bytes, data, filename)

    # 2. 类型识别（先按文件名，拿到 OCR 文本后再确认）
    doc_type = detect_document_type(filename, parsed.full_text, declared_type)

    # 3. OCR 补齐文本
    ocr_backend = get_ocr_backend()
    ocr = await ocr_backend.recognize(parsed, sidecar_text)
    if ocr.pages and any(p.strip() for p in ocr.pages):
        parsed.pages = ocr.pages
        parsed.has_text_layer = parsed.has_text_layer or ocr.backend != "stub_sidecar"
    if doc_type.value == "unknown":
        doc_type = detect_document_type(filename, parsed.full_text, declared_type)

    # 4. 落存储
    storage = get_storage()
    await storage.ensure_ready()
    storage_key = f"cases/{case_id}/{digest[:12]}_{filename}"
    await storage.put(storage_key, data, parsed.mime)

    # 5. 建文档记录
    document = Document(
        id=new_id("doc"),
        case_id=case_id,
        type=doc_type.value,
        storage_key=storage_key,
        sha256=digest,
        page_count=parsed.page_count,
    )
    session.add(document)
    await session.flush()

    for idx, text in enumerate(parsed.pages, start=1):
        image_key = None
        if idx - 1 < len(parsed.page_images) and parsed.page_images[idx - 1] is not None:
            image_key = f"{storage_key}#page{idx}"
            await storage.put(image_key, parsed.page_images[idx - 1] or b"", parsed.mime)
        session.add(
            DocumentPage(
                id=new_id("pg"),
                document_id=document.id,
                page_no=idx,
                ocr_text=text,
                image_key=image_key,
            )
        )

    await emit_trace(
        session,
        case_id=case_id,
        trace_id=trace_id,
        event_type=EVENT_DOCUMENT_UPLOADED,
        actor="document_service",
        payload={
            "document_id": document.id,
            "filename": filename,
            "sha256": digest,
            "document_type": doc_type.value,
            "page_count": parsed.page_count,
            "ocr_backend": ocr.backend,
            "ocr_degraded": ocr.degraded,
            "ocr_error": ocr.error,
        },
    )

    result = IngestResult(
        document=document,
        parsed=parsed,
        ocr_backend=ocr.backend,
        ocr_degraded=ocr.degraded,
    )
    if ocr.error:
        result.errors.append(ocr.error)

    # 6. 抽取
    extraction = await extract_document(
        session,
        gateway,
        case_id=case_id,
        document=document,
        parsed=parsed,
        trace_id=trace_id,
    )
    result.extraction = extraction
    if extraction and extraction.error:
        result.errors.append(extraction.error)

    present = {k: v.value for k, v in (extraction.fields.items() if extraction else [])}
    result.missing_fields = missing_fields(doc_type, present)
    return result
