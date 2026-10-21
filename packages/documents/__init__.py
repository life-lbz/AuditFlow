"""文档解析与抽取（文档 §7）。"""

from .extractor import CaseFields, extract_document, load_case_fields
from .ocr import OcrBackend, OcrResult, RemoteVlmOcr, SidecarOcr, TextLayerOcr, get_ocr_backend
from .parser import DocumentParseError, ParsedDocument, parse_bytes, validate
from .schema import (
    DOCUMENT_TYPE_LABELS,
    FIELD_LABELS,
    REQUIRED_DOCUMENTS,
    REQUIRED_FIELDS,
    detect_document_type,
    missing_fields,
)
from .service import IngestResult, ingest_document

__all__ = [
    "CaseFields",
    "DOCUMENT_TYPE_LABELS",
    "DocumentParseError",
    "FIELD_LABELS",
    "IngestResult",
    "OcrBackend",
    "OcrResult",
    "ParsedDocument",
    "REQUIRED_DOCUMENTS",
    "REQUIRED_FIELDS",
    "RemoteVlmOcr",
    "SidecarOcr",
    "TextLayerOcr",
    "detect_document_type",
    "extract_document",
    "get_ocr_backend",
    "ingest_document",
    "load_case_fields",
    "missing_fields",
    "parse_bytes",
    "validate",
]
