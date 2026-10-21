"""混合检索（文档 §8.3：关键词 + 向量 → RRF 融合 → rerank → top-k）。"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..gateway.base import ModelGateway
from .indexer import load_active_chunks
from .reranker import Reranker

RRF_K = 60


@dataclass
class SearchHit:
    """一条检索命中。"""

    chunk_id: str
    policy_id: str
    version: str
    section: str
    content: str
    page_no: int | None = None
    score: float = 0.0
    keyword_score: float = 0.0
    vector_score: float = 0.0
    rerank_score: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "policy_id": self.policy_id,
            "version": self.version,
            "section": self.section,
            "content": self.content,
            "page_no": self.page_no,
            "score": round(self.score, 6),
            "keyword_score": round(self.keyword_score, 6),
            "vector_score": round(self.vector_score, 6),
            "rerank_score": round(self.rerank_score, 6),
        }

    def citation(self) -> str:
        """可引用的制度出处（文档 §8.4）。"""
        page = f" 第{self.page_no}页" if self.page_no else ""
        return f"{self.policy_id} v{self.version} · {self.section}{page}"


@dataclass
class SearchResult:
    hits: list[SearchHit] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)

    @property
    def chunks(self) -> list[dict[str, Any]]:
        return [h.as_dict() for h in self.hits]


class HybridSearcher:
    """关键词 + 向量混合检索（当前数据量下在内存中融合，规模上升后可换 pgvector 索引）。"""

    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway
        self.reranker = Reranker(gateway)

    async def search(
        self,
        session: AsyncSession,
        query: str,
        *,
        top_k: int | None = None,
        category: str = "",
    ) -> SearchResult:
        settings = get_settings()
        top_k = top_k or settings.retrieval_rerank_k

        chunks = await load_active_chunks(session)
        if not chunks:
            return SearchResult(debug={"reason": "no_policy_chunks", "query": query})

        # 类型过滤：制度切片中提到该报销类型或为通用制度时保留
        if category:
            filtered = [c for c in chunks if self._matches_category(c["content"], category)]
            chunks = filtered or chunks

        # 1) 关键词召回
        keyword_ranked = self._keyword_search(query, chunks, settings.retrieval_keyword_k)

        # 2) 向量召回
        vector_ranked = await self._vector_search(query, chunks, settings.retrieval_vector_k)

        # 3) RRF 融合
        fused = self._rrf_fuse(keyword_ranked, vector_ranked, settings.retrieval_fusion_k)

        # 4) rerank（文档 §8.3 最后一步）
        reranked = await self.reranker.rerank(query, fused, top_k=top_k)

        return SearchResult(
            hits=reranked,
            debug={
                "query": query,
                "category": category,
                "total_chunks": len(chunks),
                "keyword_hits": len(keyword_ranked),
                "vector_hits": len(vector_ranked),
                "fused": len(fused),
                "returned": len(reranked),
            },
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _matches_category(content: str, category: str) -> bool:
        label = {"travel": ("差旅", "出差", "住宿", "交通"),
                 "meal": ("餐饮", "用餐", "招待"),
                 "office_supplies": ("办公", "用品", "采购")}.get(category, ())
        if not label:
            return True
        return any(k in content for k in label) or "通用" in content or "报销总则" in content

    def _keyword_search(self, query: str, chunks: list[dict[str, Any]], k: int) -> list[tuple[dict[str, Any], float]]:
        """简化 BM25：IDF 加权的词项覆盖率。"""
        q_tokens = _tokenize(query)
        if not q_tokens:
            return []
        doc_tokens = [_tokenize(c["content"]) for c in chunks]
        df: dict[str, int] = {}
        for tokens in doc_tokens:
            for t in set(tokens):
                df[t] = df.get(t, 0) + 1
        n = len(chunks)
        avg_len = _avg_len(doc_tokens) or 1.0

        scored: list[tuple[dict[str, Any], float]] = []
        for chunk, tokens in zip(chunks, doc_tokens):
            tf: dict[str, int] = {}
            for t in tokens:
                tf[t] = tf.get(t, 0) + 1
            score = 0.0
            for t in q_tokens:
                if t not in tf:
                    continue
                idf = math.log(1 + (n - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5))
                denom = tf[t] + 1.2 * (0.25 + 0.75 * len(tokens) / avg_len)
                score += idf * (tf[t] * 2.2) / denom
            if score > 0:
                scored.append((chunk, score))
        scored.sort(key=lambda x: -x[1])
        return scored[:k]

    async def _vector_search(
        self, query: str, chunks: list[dict[str, Any]], k: int
    ) -> list[tuple[dict[str, Any], float]]:
        if not any(c.get("embedding") for c in chunks):
            return []
        q_vec = (await self.gateway.embed([query]))[0]
        scored: list[tuple[dict[str, Any], float]] = []
        for chunk in chunks:
            vec = chunk.get("embedding") or []
            if not vec:
                continue
            scored.append((chunk, _cosine(q_vec, vec)))
        scored.sort(key=lambda x: -x[1])
        return scored[:k]

    @staticmethod
    def _rrf_fuse(
        keyword_ranked: list[tuple[dict[str, Any], float]],
        vector_ranked: list[tuple[dict[str, Any], float]],
        k: int,
    ) -> list[dict[str, Any]]:
        """Reciprocal Rank Fusion。"""
        scores: dict[str, float] = {}
        store: dict[str, dict[str, Any]] = {}
        kw_scores = {c["chunk_id"]: s for c, s in keyword_ranked}
        vec_scores = {c["chunk_id"]: s for c, s in vector_ranked}

        for rank, (chunk, _) in enumerate(keyword_ranked):
            cid = chunk["chunk_id"]
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
            store[cid] = chunk
        for rank, (chunk, _) in enumerate(vector_ranked):
            cid = chunk["chunk_id"]
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
            store[cid] = chunk

        ordered = sorted(scores.items(), key=lambda x: -x[1])[:k]
        result: list[dict[str, Any]] = []
        for cid, rrf in ordered:
            chunk = store[cid]
            result.append(
                {
                    **chunk,
                    "score": rrf,
                    "keyword_score": kw_scores.get(cid, 0.0),
                    "vector_score": vec_scores.get(cid, 0.0),
                }
            )
        return result


def _tokenize(text: str) -> list[str]:
    text = (text or "").lower()
    latin = re.findall(r"[a-z0-9_]+", text)
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    bigrams = [cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)]
    return latin + bigrams + cjk


def _avg_len(token_lists: list[list[str]]) -> float:
    return sum(len(t) for t in token_lists) / max(1, len(token_lists))


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
