"""文档解析（文档 §7.1：文件校验 → 类型识别 → 页面渲染 → OCR/版式解析）。

本地实现只做「结构性解析」：
- PDF：提取文本层（多页）
- 文本：直接读取
- 图片：保留原图字节，文本交由 ocr 模块补齐

需要视觉理解的部分（倾斜、模糊、复杂版面）由 OCR / VLM 负责，
本地通过 stub OCR 以确定性方式提供等价文本，真实视觉能力接入见 ocr.py 的 RemoteVlmOcr。
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

MAX_FILE_BYTES = 20 * 1024 * 1024  # 单文件上限 20MB

TEXT_EXTENSIONS = {".txt", ".md", ".text"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}
PDF_EXTENSIONS = {".pdf"}

_MIME_BY_EXT = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
}


class DocumentParseError(Exception):
    """文档解析失败（对应文档 §7.5 常见失败）。"""


@dataclass
class ParsedDocument:
    filename: str
    mime: str
    page_count: int
    pages: list[str] = field(default_factory=list)
    page_images: list[bytes | None] = field(default_factory=list)
    has_text_layer: bool = False
    error: str | None = None

    @property
    def full_text(self) -> str:
        return "\n".join(self.pages)


def guess_mime(filename: str) -> str:
    return _MIME_BY_EXT.get(Path(filename).suffix.lower(), "application/octet-stream")


def validate(data: bytes, filename: str) -> None:
    """文件校验：非空、体积、扩展名。"""
    if not data:
        raise DocumentParseError(f"文件为空: {filename}")
    if len(data) > MAX_FILE_BYTES:
        raise DocumentParseError(f"文件超过 {MAX_FILE_BYTES // 1024 // 1024}MB 限制: {filename}")
    suffix = Path(filename).suffix.lower()
    if suffix not in TEXT_EXTENSIONS | IMAGE_EXTENSIONS | PDF_EXTENSIONS:
        raise DocumentParseError(f"不支持的文档格式: {suffix or '(无扩展名)'}")


def parse_bytes(data: bytes, filename: str) -> ParsedDocument:
    """同步解析（CPU 密集，调用方应放到线程中执行）。"""
    validate(data, filename)
    suffix = Path(filename).suffix.lower()
    mime = guess_mime(filename)

    if suffix in TEXT_EXTENSIONS:
        text = data.decode("utf-8", errors="replace")
        return ParsedDocument(
            filename=filename,
            mime=mime,
            page_count=1,
            pages=[text],
            page_images=[None],
            has_text_layer=True,
        )

    if suffix in PDF_EXTENSIONS:
        return _parse_pdf(data, filename, mime)

    # 图片：保留字节，文本由 OCR 补齐
    return ParsedDocument(
        filename=filename,
        mime=mime,
        page_count=1,
        pages=[""],
        page_images=[data],
        has_text_layer=False,
    )


def _parse_pdf(data: bytes, filename: str, mime: str) -> ParsedDocument:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - 依赖缺失时给出明确提示
        raise DocumentParseError("缺少 pypdf 依赖，无法解析 PDF") from exc

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 - pypdf 抛出的异常类型很杂
        raise DocumentParseError(f"PDF 解析失败: {exc}") from exc

    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - 单页失败不应导致整份文档失败
            pages.append("")

    has_text = any(p.strip() for p in pages)
    return ParsedDocument(
        filename=filename,
        mime=mime,
        page_count=max(1, len(pages)),
        pages=pages or [""],
        page_images=[None] * max(1, len(pages)),
        has_text_layer=has_text,
    )
