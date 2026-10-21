"""API 层冒烟测试：走真实 ASGI 应用，覆盖「创建 → 上传 → 运行 → 详情 → 审批」全链路。"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.main import create_app
from packages.core.db import SessionLocal, init_db
from packages.retrieval.indexer import PolicyDocument, index_policy

from .conftest import FIXTURES_DIR, find_case_spec

EMPLOYEE_HEADERS = {"X-Role": "employee", "X-User-Id": "u_1001"}
REVIEWER_HEADERS = {"X-Role": "reviewer", "X-User-Id": "reviewer_01"}
ADMIN_HEADERS = {"X-Role": "admin", "X-User-Id": "admin_01"}


@pytest.fixture(scope="module", autouse=True)
def _prepare_db():
    """API 测试使用 db.py 的全局引擎（conftest 已设为内存 SQLite）。"""
    import asyncio

    from infra.seed.policies_data import ALL_POLICIES
    from packages.gateway.factory import get_gateway

    async def _setup():
        await init_db()
        gateway = get_gateway()
        async with SessionLocal() as session:
            for policy in ALL_POLICIES:
                await index_policy(
                    session,
                    gateway,
                    PolicyDocument(
                        policy_id=policy.policy_id,
                        title=policy.title,
                        version=policy.version,
                        effective_date=policy.effective_date,
                        category=policy.category,
                        markdown=policy.markdown,
                    ),
                )
            await session.commit()

    asyncio.run(_setup())


@pytest.fixture
async def client():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_health_and_system_info(client):
    health = await client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    info = await client.get("/system/info")
    assert info.status_code == 200
    body = info.json()
    assert body["model_provider"] == "stub"
    assert "CREATED" in body["statuses"]
    assert body["policy_chunks"] > 0


@pytest.mark.asyncio
async def test_create_case_requires_permission(client):
    payload = {"employee_id": "u_1001", "category": "travel", "amount": 560.0, "description": "上海出差"}
    # reviewer 无创建权限
    denied = await client.post("/cases", json=payload, headers=REVIEWER_HEADERS)
    assert denied.status_code == 403

    ok = await client.post("/cases", json=payload, headers=EMPLOYEE_HEADERS)
    assert ok.status_code == 201
    body = ok.json()
    assert body["status"] == "CREATED"
    assert body["id"].startswith("case_")


@pytest.mark.asyncio
async def test_full_flow_via_api(client):
    """附录 C.1 路径：创建 → 上传三份材料 → 运行 → 待审批 → 批准并提交 ERP。"""
    spec = find_case_spec("normal", category="travel")
    created = await client.post(
        "/cases",
        json={
            "employee_id": "u_1001",
            "category": spec["category"],
            "amount": float(spec["amount"]),
            "description": spec["description"],
        },
        headers=EMPLOYEE_HEADERS,
    )
    assert created.status_code == 201
    case_id = created.json()["id"]

    case_dir = FIXTURES_DIR / spec["case_id"]
    for doc in spec["documents"]:
        content = (case_dir / doc["filename"]).read_bytes()
        resp = await client.post(
            f"/cases/{case_id}/documents",
            files={"file": (doc["filename"], content, "text/plain")},
            data={"declared_type": doc["type"]},
            headers=EMPLOYEE_HEADERS,
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["document_type"] == doc["type"]

    # 详情应聚合出字段、证据与缺失材料信息
    detail = await client.get(f"/cases/{case_id}", headers=EMPLOYEE_HEADERS)
    assert detail.status_code == 200
    detail_body = detail.json()
    assert len(detail_body["documents"]) == 3
    assert detail_body["extracted_fields"]["amount"]["value"]
    assert detail_body["missing_documents"] == []

    # 运行工作流
    run = await client.post(
        f"/cases/{case_id}/run",
        json={"auto_submit": False, "mode": "single_agent_verifier"},
        headers=EMPLOYEE_HEADERS,
    )
    assert run.status_code == 200, run.text
    run_body = run.json()
    assert run_body["decision"]["decision"] == "approve"
    assert run_body["verifier_status"] == "passed"
    assert run_body["status"] == "WAITING_APPROVAL"

    # 轨迹与回放
    trace = await client.get(f"/cases/{case_id}/trace", headers=EMPLOYEE_HEADERS)
    assert trace.status_code == 200
    event_types = {e["event_type"] for e in trace.json()}
    assert {"state_transition", "document_uploaded", "policy_retrieved", "decision_made"} <= event_types

    replay = await client.get(f"/cases/{case_id}/replay", headers=EMPLOYEE_HEADERS)
    assert replay.status_code == 200
    assert replay.json()["event_count"] > 5

    # 审核员批准
    approved = await client.post(
        f"/cases/{case_id}/approvals",
        json={"action": "approve", "comment": "材料齐全，同意报销"},
        headers=REVIEWER_HEADERS,
    )
    assert approved.status_code == 201, approved.text
    assert approved.json()["action"] == "approve"

    final = await client.get(f"/cases/{case_id}", headers=REVIEWER_HEADERS)
    assert final.status_code == 200
    assert final.json()["approvals"]


@pytest.mark.asyncio
async def test_reject_flow_via_api(client):
    """驳回动作必须让案件终结且留下记录。"""
    spec = find_case_spec(["travel", "over_limit"])
    created = await client.post(
        "/cases",
        json={
            "employee_id": "u_1002",
            "category": spec["category"],
            "amount": float(spec["amount"]),
            "description": spec["description"],
        },
        headers={"X-Role": "employee", "X-User-Id": "u_1002"},
    )
    case_id = created.json()["id"]
    employee_headers = {"X-Role": "employee", "X-User-Id": "u_1002"}

    case_dir = FIXTURES_DIR / spec["case_id"]
    for doc in spec["documents"]:
        content = (case_dir / doc["filename"]).read_bytes()
        await client.post(
            f"/cases/{case_id}/documents",
            files={"file": (doc["filename"], content, "text/plain")},
            data={"declared_type": doc["type"]},
            headers=employee_headers,
        )

    run = await client.post(f"/cases/{case_id}/run", json={}, headers=employee_headers)
    assert run.status_code == 200
    assert run.json()["status"] == "WAITING_APPROVAL"

    rejected = await client.post(
        f"/cases/{case_id}/approvals",
        json={"action": "reject", "comment": "住宿超标且无特殊审批"},
        headers=REVIEWER_HEADERS,
    )
    assert rejected.status_code == 201
    assert rejected.json()["status"] == "REJECTED"


@pytest.mark.asyncio
async def test_policy_search_endpoint(client):
    resp = await client.get(
        "/policies/search",
        params={"q": "住宿 每晚 额度 上限", "category": "travel", "top_k": 3},
        headers=EMPLOYEE_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["hits"]
    assert all("policy_id" in h for h in body["hits"])
    assert body["citations"]


@pytest.mark.asyncio
async def test_pending_list_for_reviewer(client):
    resp = await client.get("/approvals/pending", headers=REVIEWER_HEADERS)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

    denied = await client.get("/approvals/pending", headers=EMPLOYEE_HEADERS)
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_employee_cannot_view_others_case(client):
    created = await client.post(
        "/cases",
        json={"employee_id": "u_2001", "category": "meal", "amount": 260.0, "description": "工作餐"},
        headers={"X-Role": "employee", "X-User-Id": "u_2001"},
    )
    case_id = created.json()["id"]

    denied = await client.get(f"/cases/{case_id}", headers=EMPLOYEE_HEADERS)
    assert denied.status_code == 403

    allowed = await client.get(f"/cases/{case_id}", headers={"X-Role": "employee", "X-User-Id": "u_2001"})
    assert allowed.status_code == 200
