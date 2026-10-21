"""远端模型网关契约测试：用 MockTransport 模拟 OpenAI 兼容服务，不需要真实模型。"""

from __future__ import annotations

import json

import httpx
import pytest

from packages.gateway.base import (
    DocumentType,
    ExtractionRequest,
    VerificationRequest,
)
from packages.gateway.remote import RemoteModelGateway


def _completion(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8},
        },
    )


def _gateway(handler, *, retries: int = 2) -> RemoteModelGateway:
    return RemoteModelGateway(
        base_url="http://model.test/v1",
        api_key="k",
        model="qwen-test",
        max_retries=retries,
        transport=httpx.MockTransport(handler),
    )


def test_missing_base_url_raises():
    with pytest.raises(RuntimeError):
        RemoteModelGateway(base_url="")


@pytest.mark.asyncio
async def test_extract_structured_parses_fenced_json():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.dumps(
            {
                "document_type": "invoice",
                "fields": {
                    "invoice_no": {"value": "INV-1", "confidence": 0.95, "page_no": 1},
                    "amount": {"value": 560.0, "confidence": 0.9, "page_no": 1, "bbox": [1, 2, 3, 4]},
                },
            },
            ensure_ascii=False,
        )
        return _completion(f"```json\n{body}\n```")

    gateway = _gateway(handler)
    result = await gateway.extract_structured(
        ExtractionRequest(document_type=DocumentType.INVOICE, pages=["发票"])
    )
    assert result.error is None
    assert result.fields["invoice_no"].value == "INV-1"
    assert result.fields["amount"].bbox == [1, 2, 3, 4]
    assert result.call.prompt_tokens == 12
    assert result.call.retry_count == 0


@pytest.mark.asyncio
async def test_extract_structured_retries_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return _completion("这不是 JSON")
        return _completion(json.dumps({"document_type": "invoice", "fields": {}}))

    gateway = _gateway(handler)
    result = await gateway.extract_structured(
        ExtractionRequest(document_type=DocumentType.INVOICE, pages=["发票"])
    )
    assert result.error is None
    assert result.call.retry_count == 1
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_extract_structured_degrades_on_persistent_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    gateway = _gateway(handler, retries=1)
    result = await gateway.extract_structured(
        ExtractionRequest(document_type=DocumentType.INVOICE, pages=["发票"])
    )
    assert result.degraded is True
    assert result.error


@pytest.mark.asyncio
async def test_choose_action_fail_safe_escalates():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"error": "unavailable"})

    gateway = _gateway(handler, retries=0)
    action = await gateway.choose_action({"case": {"id": "c1"}}, [{"name": "search_policy"}])
    assert action.type == "final"
    assert action.payload["decision"] == "escalate"
    assert action.payload["risk_level"] == "high"
    assert action.payload["failed_checks"] == ["model_unavailable"]


@pytest.mark.asyncio
async def test_choose_action_returns_tool_call():
    def handler(request: httpx.Request) -> httpx.Response:
        return _completion(
            json.dumps(
                {
                    "type": "tool",
                    "tool_name": "search_policy",
                    "arguments": {"query": "住宿额度", "top_k": 3},
                    "rationale": "确认额度",
                },
                ensure_ascii=False,
            )
        )

    gateway = _gateway(handler)
    action = await gateway.choose_action({"case": {"id": "c1"}}, [{"name": "search_policy"}])
    assert action.type == "tool"
    assert action.tool_name == "search_policy"
    assert action.arguments["query"] == "住宿额度"


@pytest.mark.asyncio
async def test_verify_fails_closed_when_model_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "down"})

    gateway = _gateway(handler, retries=0)
    result = await gateway.verify(
        VerificationRequest(case_summary="s", decision={"decision": "approve"}, evidence=[])
    )
    assert result.status == "failed"
    assert result.blocking is True
    assert result.suggested_action == "escalate_human"


@pytest.mark.asyncio
async def test_embed_and_rerank_with_fallback():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/embeddings"):
            payload = json.loads(request.content)
            vectors = [[float(len(text)), 1.0, 0.0] for text in payload["input"]]
            return httpx.Response(
                200, json={"data": [{"embedding": v} for v in vectors]}
            )
        # /rerank 不存在 → 应回退到 embedding 相关性打分
        return httpx.Response(404, json={"detail": "not found"})

    gateway = _gateway(handler)
    vectors = await gateway.embed(["abc", "de"])
    assert vectors[0][0] == 3.0

    ranked = await gateway.rerank("ab", ["abc", "xyz"])
    assert len(ranked) == 2
    assert ranked[0][0] == 0  # "abc" 与查询向量更接近


@pytest.mark.asyncio
async def test_health_reports_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    gateway = _gateway(handler)
    assert (await gateway.health())["ok"] is True

    broken = RemoteModelGateway(
        base_url="http://model.test/v1",
        transport=httpx.MockTransport(lambda request: httpx.Response(503)),
    )
    assert (await broken.health())["ok"] is False
