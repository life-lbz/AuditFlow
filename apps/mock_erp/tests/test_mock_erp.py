"""Mock ERP 接口测试（文档 §14）：幂等、提交流转、故障注入。"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from apps.mock_erp.main import app

CREATE_PAYLOAD = {
    "case_id": "case_t1",
    "employee_id": "emp_001",
    "category": "travel",
    "amount": 120.5,
    "vendor": "测试航司",
    "description": "机票",
}


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post("/admin/reset")
        yield c


async def test_create_with_idempotency_key(client: httpx.AsyncClient):
    r1 = await client.post("/expenses", json=CREATE_PAYLOAD, headers={"Idempotency-Key": "k-1"})
    assert r1.status_code == 201
    erp_id = r1.json()["erp_id"]

    # 相同幂等键重复创建：返回同一单据，不新增
    r2 = await client.post("/expenses", json=CREATE_PAYLOAD, headers={"Idempotency-Key": "k-1"})
    assert r2.status_code == 201
    assert r2.json()["erp_id"] == erp_id

    state = await client.get("/admin/state")
    assert state.json()["expense_count"] == 1


async def test_create_without_key_creates_new(client: httpx.AsyncClient):
    r1 = await client.post("/expenses", json=CREATE_PAYLOAD)
    r2 = await client.post("/expenses", json=CREATE_PAYLOAD)
    assert r1.json()["erp_id"] != r2.json()["erp_id"]


async def test_submit_flow_and_idempotent_resubmit(client: httpx.AsyncClient):
    erp_id = (await client.post("/expenses", json=CREATE_PAYLOAD)).json()["erp_id"]
    assert (await client.get(f"/expenses/{erp_id}")).json()["status"] == "draft"

    r = await client.post(f"/expenses/{erp_id}/submit")
    assert r.json()["status"] == "submitted"

    # 重复 submit 幂等返回，历史不重复追加
    r2 = await client.post(f"/expenses/{erp_id}/submit")
    assert r2.json()["status"] == "submitted"
    history = (await client.get(f"/expenses/{erp_id}/history")).json()["history"]
    assert [h["event"] for h in history] == ["created", "submitted"]


async def test_get_not_found(client: httpx.AsyncClient):
    r = await client.get("/expenses/erp_missing")
    assert r.status_code == 404


async def test_failure_server_error(client: httpx.AsyncClient):
    await client.post("/admin/failure-mode", json={"mode": "server_error"})
    r = await client.post("/expenses", json=CREATE_PAYLOAD)
    assert r.status_code == 500
    # GET 与 /admin 不受故障影响
    assert (await client.get("/admin/state")).status_code == 200


async def test_failure_invalid_payload(client: httpx.AsyncClient):
    await client.post("/admin/failure-mode", json={"mode": "invalid_payload"})
    r = await client.post("/expenses", json=CREATE_PAYLOAD)
    assert r.status_code == 422


async def test_failure_duplicate_submit_conflict(client: httpx.AsyncClient):
    await client.post("/admin/failure-mode", json={"mode": "duplicate_submit"})
    r1 = await client.post("/expenses", json=CREATE_PAYLOAD, headers={"Idempotency-Key": "k-2"})
    assert r1.status_code == 201
    r2 = await client.post("/expenses", json=CREATE_PAYLOAD, headers={"Idempotency-Key": "k-2"})
    assert r2.status_code == 409
    assert r2.json()["detail"]["existing_erp_id"] == r1.json()["erp_id"]


async def test_failure_delayed_state(client: httpx.AsyncClient):
    await client.post("/admin/failure-mode", json={"mode": "delayed_state", "state_delay_seconds": 0.3})
    erp_id = (await client.post("/expenses", json=CREATE_PAYLOAD)).json()["erp_id"]
    await client.post(f"/expenses/{erp_id}/submit")

    # 状态延迟窗口内查询仍显示 draft（模拟真实 ERP 的读写不一致）
    assert (await client.get(f"/expenses/{erp_id}")).json()["status"] == "draft"
    await asyncio.sleep(0.35)
    assert (await client.get(f"/expenses/{erp_id}")).json()["status"] == "submitted"


async def test_failure_timeout_after_commit(client: httpx.AsyncClient):
    """数据已写入但响应延迟：恢复后能通过幂等键找回单据。"""
    await client.post("/admin/failure-mode", json={"mode": "timeout_after_commit", "delay_seconds": 0.2})
    r = await client.post("/expenses", json=CREATE_PAYLOAD, headers={"Idempotency-Key": "k-3"})
    assert r.status_code == 201  # 延迟后仍返回成功
    state = (await client.get("/admin/state")).json()
    assert state["expense_count"] == 1  # 数据确实已写入
