"""生产级基线测试：配置自检、健康探针、安全头、限流、请求体上限、上传校验、案件锁。"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from apps.api.main import create_app
from packages.core.config import Settings, get_settings
from packages.core.db import init_db
from packages.core.locks import LockBusy, case_lock
from packages.documents.validation import DocumentValidationError, safe_filename, validate_upload

EMPLOYEE_HEADERS = {"X-Role": "employee", "X-User-Id": "u_1001"}


@pytest.fixture(scope="module", autouse=True)
def _prepare_db():
    asyncio.run(init_db())


@pytest_asyncio.fixture
async def make_client(monkeypatch):
    """按需注入环境变量后构建独立 app，测试结束恢复配置缓存。"""
    clients: list[AsyncClient] = []

    async def _make(env: dict[str, str]) -> AsyncClient:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()
        app = create_app()
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        clients.append(client)
        return client

    yield _make

    for client in clients:
        await client.aclose()
    get_settings.cache_clear()


def test_production_config_requires_hardening():
    bad = Settings(
        _env_file=None,
        environment="production",
        auth_mode="dev",
        auth_secret="short",
        cors_origins="*",
    )
    assert len(bad.production_errors()) == 3
    assert bad.rate_limit_active is True

    good = Settings(
        _env_file=None,
        environment="production",
        auth_mode="token",
        auth_secret="k" * 40,
        cors_origins="https://auditflow.example.com",
    )
    assert good.production_errors() == []
    assert good.production_warnings()


def test_development_environment_is_not_blocked():
    dev = Settings(_env_file=None, environment="development", auth_mode="dev")
    assert dev.production_errors() == []
    assert dev.rate_limit_active is False


def test_safe_filename_and_magic_bytes():
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("invoice/发票 2024.PDF") == "发票_2024.pdf"

    png = b"\x89PNG\r\n\x1a\n" + b"0" * 8
    assert validate_upload("invoice.png", png) == ".png"

    with pytest.raises(DocumentValidationError):
        validate_upload("invoice.png", b"this is definitely not a png")

    with pytest.raises(DocumentValidationError):
        validate_upload("note.txt", b"hello\x00binary")

    assert validate_upload("note.txt", "报销说明".encode("utf-8")) == ".txt"


@pytest.mark.asyncio
async def test_case_lock_is_exclusive():
    async with case_lock("case_lock_demo"):
        with pytest.raises(LockBusy):
            async with case_lock("case_lock_demo"):
                pass

    # 退出后可以再次获取
    async with case_lock("case_lock_demo"):
        pass


@pytest.mark.asyncio
async def test_health_probes_and_security_headers(make_client):
    client = await make_client({})

    live = await client.get("/health/live")
    assert live.status_code == 200
    assert live.json()["status"] == "alive"
    assert live.headers["X-Content-Type-Options"] == "nosniff"
    assert live.headers["X-Frame-Options"] == "DENY"
    assert live.headers["X-Request-ID"].startswith("req_")

    ready = await client.get("/health/ready")
    assert ready.status_code == 200
    body = ready.json()
    assert body["status"] in {"ready", "degraded"}
    assert body["checks"]["database"]["ok"] is True


@pytest.mark.asyncio
async def test_rate_limit_returns_429(make_client):
    client = await make_client(
        {
            "RATE_LIMIT_ENABLED": "true",
            "RATE_LIMIT_WRITES_PER_MINUTE": "2",
            "TRUST_PROXY_HEADERS": "true",
            "RATE_LIMIT_BACKEND": "memory",
            "CASE_LOCK_BACKEND": "memory",
        }
    )
    payload = {"employee_id": "u_1001", "category": "meal", "amount": 100.0, "description": "限流测试"}
    headers = {**EMPLOYEE_HEADERS, "X-Real-IP": "10.9.9.9"}

    first = await client.post("/cases", json=payload, headers=headers)
    second = await client.post("/cases", json=payload, headers=headers)
    third = await client.post("/cases", json=payload, headers=headers)

    assert first.status_code == 201
    assert second.status_code == 201
    assert third.status_code == 429
    assert third.headers["Retry-After"].isdigit()
    assert third.json()["code"] == "rate_limited"


@pytest.mark.asyncio
async def test_payload_too_large_is_rejected(make_client):
    client = await make_client({"MAX_REQUEST_MB": "1", "MAX_UPLOAD_MB": "1"})
    big_description = "x" * (1024 * 1024 + 100)
    response = await client.post(
        "/cases",
        json={"employee_id": "u_1001", "category": "meal", "amount": 1.0, "description": big_description},
        headers=EMPLOYEE_HEADERS,
    )
    assert response.status_code == 413
    assert response.json()["code"] == "payload_too_large"


@pytest.mark.asyncio
async def test_upload_rejects_spoofed_extension(make_client):
    client = await make_client({})
    created = await client.post(
        "/cases",
        json={"employee_id": "u_1001", "category": "meal", "amount": 66.0, "description": "上传校验"},
        headers=EMPLOYEE_HEADERS,
    )
    assert created.status_code == 201
    case_id = created.json()["id"]

    response = await client.post(
        f"/cases/{case_id}/documents",
        files={"file": ("fake.png", b"not-a-real-image", "image/png")},
        headers=EMPLOYEE_HEADERS,
    )
    assert response.status_code == 400
    assert "不符" in response.json()["detail"]


@pytest.mark.asyncio
async def test_metrics_token_protection(make_client):
    client = await make_client({"METRICS_TOKEN": "metrics-secret"})

    denied = await client.get("/metrics")
    assert denied.status_code == 401

    allowed = await client.get("/metrics", headers={"Authorization": "Bearer metrics-secret"})
    assert allowed.status_code == 200
    assert "auditflow_" in allowed.text
