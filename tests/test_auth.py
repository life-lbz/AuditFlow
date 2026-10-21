"""身份认证、错误语义与上传限制测试（P0 安全基线）。"""

from __future__ import annotations

import asyncio
import time

import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.main import create_app
from packages.core.auth import AuthError, create_token, decode_token
from packages.core.config import get_settings
from packages.core.db import init_db

EMPLOYEE_HEADERS = {"X-Role": "employee", "X-User-Id": "u_1001"}
FORGED_ADMIN_HEADERS = {"X-Role": "admin", "X-User-Id": "attacker"}


@pytest.fixture(scope="module", autouse=True)
def _prepare_db():
    asyncio.run(init_db())


@pytest.fixture
async def client():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def token_mode(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "token")
    monkeypatch.setenv("AUTH_SECRET", "unit-test-secret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_token_roundtrip_and_tamper():
    token = create_token("u_1001", "reviewer", ttl_seconds=60)
    payload = decode_token(token)
    assert payload["sub"] == "u_1001"
    assert payload["role"] == "reviewer"

    body, signature = token.split(".")
    with pytest.raises(AuthError):
        decode_token(f"{body}.{signature[:-2]}xx")
    with pytest.raises(AuthError):
        decode_token("not-a-token")


def test_token_expiry():
    expired = create_token("u_1001", "employee", ttl_seconds=5, now=time.time() - 120)
    with pytest.raises(AuthError):
        decode_token(expired)


@pytest.mark.asyncio
async def test_dev_mode_accepts_role_header(client):
    resp = await client.get("/cases", headers=EMPLOYEE_HEADERS)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_dev_mode_can_issue_token(client):
    resp = await client.post("/auth/token", json={"user_id": "reviewer_01", "role": "reviewer"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert decode_token(body["access_token"])["role"] == "reviewer"


@pytest.mark.asyncio
async def test_token_mode_rejects_forged_header(client, token_mode):
    # 只带伪造的 X-Role，没有 Bearer Token → 401
    resp = await client.get("/cases", headers=FORGED_ADMIN_HEADERS)
    assert resp.status_code == 401

    # 合法 token 可用；此时伪造头被忽略，身份仍来自 token
    token = create_token("reviewer_01", "reviewer")
    resp = await client.get(
        "/cases",
        headers={"Authorization": f"Bearer {token}", **FORGED_ADMIN_HEADERS},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_token_mode_blocks_employee_approval(client, token_mode):
    token = create_token("u_1001", "employee")
    resp = await client.get("/approvals/pending", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_token_mode_disables_issue_endpoint(client, token_mode):
    resp = await client.post("/auth/token", json={"user_id": "x", "role": "admin"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_permission_denied_is_403_not_500(client, token_mode):
    token = create_token("u_1001", "employee")
    resp = await client.get("/policies", headers={"Authorization": f"Bearer {token}"})
    # 员工可读制度；此处只验证不返回 5xx
    assert resp.status_code < 500


@pytest.mark.asyncio
async def test_error_response_hides_internals(client):
    resp = await client.get("/cases/does-not-exist")
    assert resp.status_code == 404
    assert resp.headers.get("X-Request-ID")
    assert "Traceback" not in resp.text


@pytest.mark.asyncio
async def test_metrics_endpoint(client):
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "auditflow_cases_total" in resp.text
    assert "auditflow_http_requests_total" in resp.text


@pytest.mark.asyncio
async def test_upload_rejects_oversize_file(client, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "0")
    get_settings.cache_clear()
    try:
        created = await client.post(
            "/cases",
            json={"employee_id": "u_1001", "category": "travel", "amount": 100.0, "description": "大文件"},
            headers=EMPLOYEE_HEADERS,
        )
        assert created.status_code == 201
        case_id = created.json()["id"]

        resp = await client.post(
            f"/cases/{case_id}/documents",
            files={"file": ("invoice.txt", b"x" * 2048, "text/plain")},
            headers=EMPLOYEE_HEADERS,
        )
        assert resp.status_code == 413
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_upload_rejects_empty_file(client):
    created = await client.post(
        "/cases",
        json={"employee_id": "u_1001", "category": "travel", "amount": 100.0, "description": "空文件"},
        headers=EMPLOYEE_HEADERS,
    )
    case_id = created.json()["id"]
    resp = await client.post(
        f"/cases/{case_id}/documents",
        files={"file": ("invoice.txt", b"", "text/plain")},
        headers=EMPLOYEE_HEADERS,
    )
    assert resp.status_code == 400
