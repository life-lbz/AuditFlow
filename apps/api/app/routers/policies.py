"""制度与检索 API（文档 §8）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.db import get_session
from packages.core.models import PolicyChunk
from packages.core.permissions import Action
from packages.gateway.factory import get_gateway
from packages.retrieval.hybrid_search import HybridSearcher

from ..deps import get_role, require

router = APIRouter(tags=["policies"])


@router.get("/policies")
async def list_policies(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """制度清单与切片统计。"""
    rows = (
        await session.execute(
            select(
                PolicyChunk.policy_id,
                PolicyChunk.version,
                PolicyChunk.effective_date,
                PolicyChunk.expiry_date,
                func.count(PolicyChunk.id).label("chunks"),
            ).group_by(
                PolicyChunk.policy_id,
                PolicyChunk.version,
                PolicyChunk.effective_date,
                PolicyChunk.expiry_date,
            )
        )
    ).all()
    return {
        "policies": [
            {
                "policy_id": r.policy_id,
                "version": r.version,
                "effective_date": r.effective_date,
                "expiry_date": r.expiry_date,
                "chunk_count": r.chunks,
            }
            for r in rows
        ]
    }


@router.get("/policies/search")
async def search_policies(
    q: str = Query(min_length=1, description="检索查询"),
    category: str = Query(default="", description="报销类型过滤"),
    top_k: int = Query(default=5, ge=1, le=20),
    session: AsyncSession = Depends(get_session),
    role=Depends(get_role),
) -> dict[str, Any]:
    """混合检索调试接口：返回分数分解，便于验证召回与重排效果。"""
    require(role, Action.VIEW_TRACE)
    searcher = HybridSearcher(get_gateway())
    result = await searcher.search(session, q, top_k=top_k, category=category)
    return {
        "query": q,
        "category": category,
        "debug": result.debug,
        "hits": [hit.as_dict() for hit in result.hits],
        "citations": [hit.citation() for hit in result.hits],
    }


@router.get("/policies/{policy_id}/chunks")
async def list_policy_chunks(
    policy_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(PolicyChunk).where(PolicyChunk.policy_id == policy_id).order_by(PolicyChunk.section)
        )
    ).scalars().all()
    return {
        "policy_id": policy_id,
        "chunks": [
            {
                "id": c.id,
                "version": c.version,
                "section": c.section,
                "content": c.content,
                "page_no": c.page_no,
                "effective_date": c.effective_date,
                "expiry_date": c.expiry_date,
            }
            for c in rows
        ],
    }
