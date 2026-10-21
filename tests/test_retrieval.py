"""混合检索测试（文档 §8）：制度切分/索引、BM25+向量召回、RRF 融合、过期过滤、rerank 降级。"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from packages.core.models import PolicyChunk
from packages.retrieval.hybrid_search import RRF_K, HybridSearcher
from packages.retrieval.indexer import (
    PolicyDocument,
    index_policy,
    load_active_chunks,
    split_by_length,
    split_sections,
)
from packages.retrieval.reranker import Reranker

MARKDOWN = """# 测试制度

## 一、住宿标准

住宿费每晚上限 500 元，按实际住宿晚数计算。
住宿费发票需注明入住日期与住宿晚数。

## 二、审批要求

单笔差旅报销超过 1000 元需提供部门负责人审批记录。
"""

MARKDOWN_V2 = """# 测试制度

## 一、住宿标准

住宿费每晚上限 600 元，按实际住宿晚数计算。

## 二、审批要求

单笔差旅报销超过 1500 元需提供部门负责人审批记录。
"""


def _chunk(chunk_id: str, content: str = "住宿费每晚上限 500 元") -> dict[str, Any]:
    """构造一条制度切片（_rrf_fuse 只依赖 chunk_id 与元数据）。"""
    return {
        "chunk_id": chunk_id,
        "policy_id": f"policy_{chunk_id}",
        "version": "1.0",
        "section": "一、住宿标准",
        "content": content,
        "page_no": None,
        "embedding": [],
    }


def _fused(chunk_id: str, score: float, content: str = "内容") -> dict[str, Any]:
    """构造 rerank 输入（RRF 融合后的候选）。"""
    return {
        **_chunk(chunk_id, content),
        "score": score,
        "keyword_score": 0.5,
        "vector_score": 0.4,
    }


# ---------------------------------------------------------------------------
# 制度切分与索引
# ---------------------------------------------------------------------------


def test_split_sections_by_markdown_headings():
    """按 Markdown 标题切分章节，标题作为 section 名。"""
    sections = split_sections(MARKDOWN)

    assert [s.section for s in sections] == ["一、住宿标准", "二、审批要求"]
    assert "每晚上限 500 元" in sections[0].content
    assert "1000 元" in sections[1].content


def test_split_by_length_aggregates_sentences():
    """超长正文按句号聚合切分，单块不超过最大字符数。"""
    sentence = "住宿费每晚上限五百元，按实际住宿晚数计算并保留发票。"
    text = sentence * 20
    chunks = split_by_length(text, max_chars=100)

    assert len(chunks) > 1
    assert all(len(chunk) <= 100 for chunk in chunks)
    assert "".join(chunks) == text


async def test_index_policy_persists_chunks_with_embeddings(session, gateway):
    """制度入库：按章节写入切片、生成向量，并以版本维度替换旧切片。"""
    policy = PolicyDocument(
        policy_id="policy_test_index",
        title="测试制度",
        version="1.0",
        effective_date="2026-01-01",
        category="travel",
        markdown=MARKDOWN,
    )
    count = await index_policy(session, gateway, policy)
    await session.commit()

    assert count == 2
    rows = (
        await session.execute(
            select(PolicyChunk).where(PolicyChunk.policy_id == "policy_test_index")
        )
    ).scalars().all()
    assert len(rows) == 2
    assert {row.section for row in rows} == {"一、住宿标准", "二、审批要求"}
    assert all(len(row.embedding) == 256 for row in rows)
    assert all(row.effective_date == "2026-01-01" for row in rows)

    # 同版本重新入库时应替换而非追加
    replaced = await index_policy(
        session,
        gateway,
        PolicyDocument(
            policy_id="policy_test_index",
            title="测试制度",
            version="1.0",
            effective_date="2026-01-01",
            category="travel",
            markdown=MARKDOWN_V2,
        ),
    )
    await session.commit()
    assert replaced == 2
    rows = (
        await session.execute(
            select(PolicyChunk).where(PolicyChunk.policy_id == "policy_test_index")
        )
    ).scalars().all()
    assert len(rows) == 2
    assert any("上限 600 元" in row.content for row in rows)
    assert all("上限 500 元" not in row.content for row in rows)


async def test_load_active_chunks_excludes_expired_and_future(session, gateway):
    """生效/失效日期过滤：过期与未生效制度不参与召回，边界当日视为有效。"""
    today = date.today()
    cases = {
        "policy_active": ((today - timedelta(days=30)).isoformat(), (today + timedelta(days=30)).isoformat()),
        "policy_expired": ((today - timedelta(days=365)).isoformat(), (today - timedelta(days=1)).isoformat()),
        "policy_boundary": ((today - timedelta(days=10)).isoformat(), today.isoformat()),
        "policy_future": ((today + timedelta(days=10)).isoformat(), None),
    }
    for policy_id, (effective, expiry) in cases.items():
        await index_policy(
            session,
            gateway,
            PolicyDocument(
                policy_id=policy_id,
                title=policy_id,
                version="1.0",
                effective_date=effective,
                expiry_date=expiry,
                category="travel",
                markdown=f"## 一、适用范围\n\n{policy_id} 的制度条款内容，用于验证生效日期过滤。\n",
            ),
        )
    await session.commit()

    chunks = await load_active_chunks(session, today=today)
    assert {chunk["policy_id"] for chunk in chunks} == {"policy_active", "policy_boundary"}

    # 过期版本仍保留在库中（文档 §8.2：保留但默认不检索）
    all_rows = (await session.execute(select(PolicyChunk))).scalars().all()
    assert len(all_rows) == 4


async def test_search_ignores_expired_policy_content(session, gateway):
    """检索层不召回过期制度：库中有切片，但搜索结果为空。"""
    today = date.today()
    await index_policy(
        session,
        gateway,
        PolicyDocument(
            policy_id="policy_old",
            title="旧版制度",
            version="1.0",
            effective_date=(today - timedelta(days=365)).isoformat(),
            expiry_date=(today - timedelta(days=1)).isoformat(),
            category="travel",
            markdown="## 一、旧版补贴\n\n量子旅行补贴每天上限 999 元，该条款已停止执行。\n",
        ),
    )
    await index_policy(
        session,
        gateway,
        PolicyDocument(
            policy_id="policy_new",
            title="现行制度",
            version="2.0",
            effective_date=(today - timedelta(days=10)).isoformat(),
            category="travel",
            markdown="## 一、基本原则\n\n所有报销必须真实、必要、合规，且与实际业务相符。\n",
        ),
    )
    await session.commit()

    result = await HybridSearcher(gateway).search(session, "量子旅行补贴", top_k=5)

    assert result.debug["total_chunks"] == 1
    assert all(hit.policy_id != "policy_old" for hit in result.hits)
    assert all("量子旅行补贴" not in hit.content for hit in result.hits)


# ---------------------------------------------------------------------------
# 混合检索与 RRF 融合
# ---------------------------------------------------------------------------


async def test_hybrid_search_hits_relevant_policy(indexed_policies, gateway):
    """BM25 + 向量混合检索能命中住宿额度条款，并返回可引用出处。"""
    result = await HybridSearcher(gateway).search(
        indexed_policies, "住宿费 每晚上限 标准", top_k=5
    )

    assert result.hits
    assert len(result.hits) <= 5
    assert result.debug["keyword_hits"] > 0
    assert result.debug["vector_hits"] > 0
    assert result.debug["returned"] == len(result.hits)
    assert any(hit.policy_id == "policy_travel" for hit in result.hits)
    assert "住宿" in result.hits[0].content
    assert result.hits[0].score > 0
    assert result.hits[0].rerank_score > 0
    assert "policy_travel" in result.hits[0].citation()


def test_rrf_fusion_orders_by_reciprocal_rank():
    """同时被关键词与向量召回的切片排名应高于单路召回。"""
    chunk_a = _chunk("a")
    chunk_b = _chunk("b")

    fused = HybridSearcher._rrf_fuse([(chunk_a, 9.0)], [(chunk_b, 0.9), (chunk_a, 0.8)], k=10)

    assert [item["chunk_id"] for item in fused] == ["a", "b"]
    assert fused[0]["score"] == pytest.approx(1.0 / (RRF_K + 1) + 1.0 / (RRF_K + 2))
    assert fused[1]["score"] == pytest.approx(1.0 / (RRF_K + 1))
    assert fused[0]["keyword_score"] == 9.0
    assert fused[0]["vector_score"] == 0.8
    assert fused[1]["keyword_score"] == 0.0


def test_rrf_fusion_truncates_to_k():
    """融合结果按 k 截断，且同一 chunk 不会被重复计入。"""
    chunk_a = _chunk("a")
    chunk_b = _chunk("b")

    fused = HybridSearcher._rrf_fuse([(chunk_a, 1.0)], [(chunk_b, 1.0), (chunk_a, 1.0)], k=1)

    assert [item["chunk_id"] for item in fused] == ["a"]
    assert len({item["chunk_id"] for item in fused}) == len(fused)


# ---------------------------------------------------------------------------
# rerank 与降级
# ---------------------------------------------------------------------------


async def test_reranker_uses_gateway_ranking(gateway):
    """正常路径：网关重排结果决定顺序与分数。"""
    fused = [
        _fused("a", 0.9, "餐饮人均上限 100 元，超出部分由个人承担。"),
        _fused("b", 0.8, "住宿费每晚上限 500 元，按实际住宿晚数计算。"),
    ]
    hits = await Reranker(gateway).rerank("住宿 上限", fused, top_k=1)

    assert [hit.chunk_id for hit in hits] == ["b"]
    assert hits[0].rerank_score > 0
    assert hits[0].keyword_score == 0.5
    assert hits[0].vector_score == 0.4


async def test_reranker_falls_back_when_gateway_raises(gateway, monkeypatch):
    """重排服务异常时降级为 RRF 融合顺序，链路不中断。"""
    async def boom(*args: Any, **kwargs: Any) -> list[tuple[int, float]]:
        raise RuntimeError("rerank service unavailable")

    monkeypatch.setattr(gateway, "rerank", boom)
    fused = [_fused("a", 0.9), _fused("b", 0.8), _fused("c", 0.7)]

    hits = await Reranker(gateway).rerank("住宿 上限", fused, top_k=2)

    assert [hit.chunk_id for hit in hits] == ["a", "b"]
    assert [hit.score for hit in hits] == [0.9, 0.8]
    assert [hit.rerank_score for hit in hits] == [0.9, 0.8]


async def test_reranker_falls_back_when_gateway_returns_empty(gateway, monkeypatch):
    """重排返回空列表时同样降级为融合分，而不是返回空结果。"""
    async def empty(*args: Any, **kwargs: Any) -> list[tuple[int, float]]:
        return []

    monkeypatch.setattr(gateway, "rerank", empty)
    fused = [_fused("a", 0.9), _fused("b", 0.8)]

    hits = await Reranker(gateway).rerank("住宿 上限", fused, top_k=2)

    assert [hit.chunk_id for hit in hits] == ["a", "b"]
    assert hits[0].score == 0.9
    assert hits[0].rerank_score == 0.9


async def test_reranker_returns_empty_for_empty_candidates(gateway):
    """无候选时直接返回空，不调用网关。"""
    assert await Reranker(gateway).rerank("任意查询", [], top_k=5) == []
