"""检索重排（文档 §8.3 最后一步）。

优先使用网关的 rerank 能力；网关不可用或返回空时，退回 RRF 融合分，
保证检索链路不会因为重排服务故障而中断（降级需记录在 debug 中）。
"""

from __future__ import annotations

from typing import Any

from ..gateway.base import ModelGateway


class Reranker:
    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway

    async def rerank(self, query: str, fused: list[dict[str, Any]], top_k: int = 5) -> list[Any]:
        from .hybrid_search import SearchHit

        if not fused:
            return []

        candidates = [c["content"] for c in fused]
        try:
            ranked = await self.gateway.rerank(query, candidates, top_k=top_k)
        except Exception:  # noqa: BLE001 - 重排失败降级到融合分
            ranked = [(i, fused[i]["score"]) for i in range(min(top_k, len(fused)))]

        if not ranked:
            ranked = [(i, fused[i]["score"]) for i in range(min(top_k, len(fused)))]

        hits: list[SearchHit] = []
        for idx, score in ranked:
            if idx < 0 or idx >= len(fused):
                continue
            chunk = fused[idx]
            hits.append(
                SearchHit(
                    chunk_id=chunk["chunk_id"],
                    policy_id=chunk["policy_id"],
                    version=chunk["version"],
                    section=chunk["section"],
                    content=chunk["content"],
                    page_no=chunk.get("page_no"),
                    score=float(score),
                    keyword_score=float(chunk.get("keyword_score", 0.0)),
                    vector_score=float(chunk.get("vector_score", 0.0)),
                    rerank_score=float(score),
                )
            )
        return hits
