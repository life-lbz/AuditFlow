"""上传文件安全校验：文件名清洗 + 扩展名白名单 + 文件头魔数校验。

只信任扩展名会让攻击者上传伪装成图片的可执行内容；这里按文件头判断真实类型，
并把文件名收敛为安全字符，避免 ``../`` 之类路径片段进入对象存储 key。
"""

from __future__ import annotations

import re
from pathlib import Path

ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}

_TEXT_EXTENSIONS = {".txt", ".md"}

_SAFE_NAME = re.compile(r"[^\w.\-]")


class DocumentValidationError(ValueError):
    """上传内容与声明类型不匹配，或文件名非法。"""


def safe_filename(filename: str | None) -> str:
    """返回可安全落库/落存储的文件名（保留扩展名，最多 120 字符）。"""
    raw = (filename or "").replace("\\", "/")
    name = Path(raw).name.strip()
    if not name:
        return "upload.bin"

    stem, suffix = name.rsplit(".", 1) if "." in name else (name, "")
    stem = _SAFE_NAME.sub("_", stem).strip("._") or "upload"
    stem = stem[:100]
    suffix = _SAFE_NAME.sub("", suffix.lower())
    return f"{stem}.{suffix}" if suffix else stem


def _matches(suffix: str, data: bytes) -> bool:
    if suffix == ".pdf":
        return data.startswith(b"%PDF-")
    if suffix == ".png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix in {".jpg", ".jpeg"}:
        return data.startswith(b"\xff\xd8\xff")
    if suffix == ".gif":
        return data.startswith(b"GIF8")
    if suffix == ".webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if suffix == ".bmp":
        return data.startswith(b"BM")
    if suffix == ".tiff":
        return data.startswith(b"II*\x00") or data.startswith(b"MM\x00*")
    if suffix in _TEXT_EXTENSIONS:
        if b"\x00" in data:
            return False
        for encoding in ("utf-8", "gbk"):
            try:
                data.decode(encoding)
                return True
            except UnicodeDecodeError:
                continue
        return False
    return False


def validate_upload(filename: str | None, data: bytes) -> str:
    """校验扩展名与文件头，返回用于后续处理的规范化文件名后缀。

    Raises:
        DocumentValidationError: 扩展名不支持，或内容与扩展名不符。
    """
    name = safe_filename(filename)
    suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if suffix not in ALLOWED_EXTENSIONS:
        raise DocumentValidationError(f"不支持的文档格式: {suffix or '(无扩展名)'}")
    if not data:
        raise DocumentValidationError("文件内容为空")
    if not _matches(suffix, data):
        raise DocumentValidationError(f"文件内容与扩展名 {suffix} 不符，已拒绝上传")
    return suffix
