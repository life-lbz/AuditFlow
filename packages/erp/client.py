"""Mock ERP HTTP 客户端（文档 §14.2 API 契约）。"""

from __future__ import annotations

import functools
from typing import Any

import httpx

from ..core.config import get_settings


@functools.lru_cache(maxsize=1)
def _default_transport() -> httpx.AsyncBaseTransport | None:
    """MOCK_ERP_INPROCESS=1 时走进程内 ASGI，避免测试依赖 Docker 中的 Mock ERP 服务。"""
    if not get_settings().mock_erp_inprocess:
        return None
    from apps.mock_erp.main import app as mock_erp_app  # 延迟导入，避免 packages 反向依赖 apps

    return httpx.ASGITransport(app=mock_erp_app)


class ErpClientError(Exception):
    def __init__(self, message: str, status_code: int | None = None, payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}


class ErpClient:
    """调用 Mock ERP。把「HTTP 200」与「业务成功」区分开，交由上层判断（文档 §14.4）。"""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or get_settings().mock_erp_url).rstrip("/")
        self.timeout = timeout
        self._transport = transport

    def _new_client(self, timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=timeout or self.timeout,
            transport=self._transport or _default_transport(),
        )

    async def create_expense(self, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        async with self._new_client() as client:
            resp = await client.post(
                f"{self.base_url}/expenses",
                json=payload,
                headers={"Idempotency-Key": idempotency_key},
            )
        if resp.status_code >= 400:
            raise ErpClientError(
                f"ERP 创建报销单失败: HTTP {resp.status_code}",
                status_code=resp.status_code,
                payload=_safe_json(resp),
            )
        return resp.json()

    async def submit_expense(self, erp_id: str) -> dict[str, Any]:
        async with self._new_client() as client:
            resp = await client.post(f"{self.base_url}/expenses/{erp_id}/submit")
        if resp.status_code >= 400:
            raise ErpClientError(
                f"ERP 提交失败: HTTP {resp.status_code}",
                status_code=resp.status_code,
                payload=_safe_json(resp),
            )
        return resp.json()

    async def get_expense(self, erp_id: str) -> dict[str, Any]:
        async with self._new_client() as client:
            resp = await client.get(f"{self.base_url}/expenses/{erp_id}")
        if resp.status_code == 404:
            raise ErpClientError("ERP 单据不存在", status_code=404)
        if resp.status_code >= 400:
            raise ErpClientError(
                f"ERP 查询失败: HTTP {resp.status_code}",
                status_code=resp.status_code,
                payload=_safe_json(resp),
            )
        return resp.json()

    async def get_history(self, erp_id: str) -> dict[str, Any]:
        async with self._new_client() as client:
            resp = await client.get(f"{self.base_url}/expenses/{erp_id}/history")
        if resp.status_code >= 400:
            raise ErpClientError(f"ERP 历史查询失败: HTTP {resp.status_code}", status_code=resp.status_code)
        return resp.json()

    async def set_failure_mode(self, mode: str, **kwargs: Any) -> dict[str, Any]:
        async with self._new_client() as client:
            resp = await client.post(
                f"{self.base_url}/admin/failure-mode",
                json={"mode": mode, **kwargs},
            )
        resp.raise_for_status()
        return resp.json()

    async def reset(self) -> dict[str, Any]:
        async with self._new_client() as client:
            resp = await client.post(f"{self.base_url}/admin/reset")
        resp.raise_for_status()
        return resp.json()

    async def health(self) -> bool:
        try:
            async with self._new_client(timeout=3.0) as client:
                resp = await client.get(f"{self.base_url}/admin/state")
            return resp.status_code < 500
        except Exception:  # noqa: BLE001
            return False


def _safe_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}
    except Exception:  # noqa: BLE001
        return {"text": resp.text[:300]}
