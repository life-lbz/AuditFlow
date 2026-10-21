"""制度入库（文档 §8.1 / §8.2）。

流程：制度文本 → 版本识别 → 章节切分 → 元数据标注 → embedding → 入库。
过期版本保留但默认不参与检索（文档 §8.2）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.models import PolicyChunk
from ..core.tracing import new_id
from ..gateway.base import ModelGateway

MAX_CHUNK_CHARS = 400
MIN_CHUNK_CHARS = 30

_SECTION_RE = re.compile(r"^(#{1,4})\s*(.+)$", re.MULTILINE)


@dataclass
class PolicySection:
    """制度中的一个章节。"""

    section: str
    content: str
    page_no: int | None = None


@dataclass
class PolicyDocument:
    """待入库的制度文档。"""

    policy_id: str
    title: str
    version: str = "1.0"
    effective_date: str | None = None
    expiry_date: str | None = None
    category: str = ""
    markdown: str = ""
    sections: list[PolicySection] = field(default_factory=list)


def split_sections(markdown: str) -> list[PolicySection]:
    """按 Markdown 标题切分章节；无标题时按段落聚合切分。"""
    matches = list(_SECTION_RE.finditer(markdown))
    if not matches:
        return [
            PolicySection(section="正文", content=chunk)
            for chunk in split_by_length(markdown)
            if chunk.strip()
        ]

    sections: list[PolicySection] = []
    for i, m in enumerate(matches):
        title = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        body = markdown[start:end].strip()
        if not body:
            continue
        for idx, piece in enumerate(split_by_length(body)):
            label = title if idx == 0 else f"{title}({idx + 1})"
            sections.append(PolicySection(section=label, content=piece))
    return sections


def split_by_length(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """按句号/换行聚合切分，避免切断句子。"""
    raw_parts = re.split(r"(?<=[。；\n])", text)
    chunks: list[str] = []
    buffer = ""
    for part in raw_parts:
        if not part.strip():
            continue
        if len(buffer) + len(part) <= max_chars:
            buffer += part
        else:
            if buffer.strip():
                chunks.append(buffer.strip())
            buffer = part
    if buffer.strip():
        chunks.append(buffer.strip())
    return [c for c in chunks if len(c) >= MIN_CHUNK_CHARS] or [c for c in chunks if c.strip()]


async def index_policy(
    session: AsyncSession,
    gateway: ModelGateway,
    policy: PolicyDocument,
    *,
    replace: bool = True,
) -> int:
    """把一份制度切分、向量化并写入 policy_chunks。返回写入的切片数。"""
    sections = policy.sections or split_sections(policy.markdown)
    if not sections:
        return 0

    if replace:
        await session.execute(
            delete(PolicyChunk).where(
                PolicyChunk.policy_id == policy.policy_id,
                PolicyChunk.version == policy.version,
            )
        )

    contents = [s.content for s in sections]
    vectors = await gateway.embed(contents)

    for section, content, vector in zip(sections, contents, vectors):
        session.add(
            PolicyChunk(
                id=new_id("pc"),
                policy_id=policy.policy_id,
                version=policy.version,
                section=section.section,
                content=content,
                embedding=vector,
                effective_date=policy.effective_date,
                expiry_date=policy.expiry_date,
                page_no=section.page_no,
            )
        )
    await session.flush()
    return len(sections)


async def load_active_chunks(
    session: AsyncSession,
    *,
    today: date | None = None,
    policy_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """加载当前有效的制度切片（文档 §8.2：过期版本不参与默认检索）。"""
    today = today or date.today()
    stmt = select(PolicyChunk)
    if policy_ids:
        stmt = stmt.where(PolicyChunk.policy_id.in_(policy_ids))
    rows = (await session.execute(stmt)).scalars().all()

    active: list[dict[str, Any]] = []
    for row in rows:
        if row.expiry_date and _parse_date(row.expiry_date) and _parse_date(row.expiry_date) < today:
            continue
        if row.effective_date and _parse_date(row.effective_date) and _parse_date(row.effective_date) > today:
            continue
        active.append(
            {
                "chunk_id": row.id,
                "policy_id": row.policy_id,
                "version": row.version,
                "section": row.section,
                "content": row.content,
                "embedding": row.embedding or [],
                "page_no": row.page_no,
                "effective_date": row.effective_date,
                "expiry_date": row.expiry_date,
            }
        )
    return active


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    text = str(raw).strip().replace("/", "-")
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y%m%d"):
        try:
            from datetime import datetime

            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None
