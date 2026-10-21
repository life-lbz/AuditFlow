"""只读工具：检索制度条款（文档 §11.1 search_policy）。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..registry import BaseTool, ToolContext, ToolSpec, ToolPermission
from ...retrieval.hybrid_search import HybridSearcher


class SearchPolicyArgs(BaseModel):
    query: str = Field(min_length=1, description="检索查询，例如「差旅 住宿 额度 上限」")
    top_k: int = Field(default=5, ge=1, le=20, description="返回条款数量")


class SearchPolicy(BaseTool):
    spec = ToolSpec(
        name="search_policy",
        description="在公司报销制度中检索相关条款，返回带出处（policy_id/版本/章节/页码）的原文片段",
        permission=ToolPermission.READ_ONLY,
        timeout_ms=15_000,
        max_retries=1,
        idempotent=True,
    )
    args_model = SearchPolicyArgs

    async def run(self, args: SearchPolicyArgs, ctx: ToolContext) -> dict:
        searcher = HybridSearcher(ctx.gateway)
        result = await searcher.search(
            ctx.session,
            args.query,
            top_k=args.top_k,
            category=ctx.case.category,
        )
        return {
            "query": args.query,
            "hits": [hit.as_dict() for hit in result.hits],
            "citations": [hit.citation() for hit in result.hits],
            "debug": result.debug,
        }
