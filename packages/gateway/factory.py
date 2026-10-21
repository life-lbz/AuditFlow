"""模型网关工厂：按 provider 返回实现实例（进程内单例）。

切换真实模型只需设置环境变量：
    MODEL_PROVIDER=openai_compatible
    MODEL_BASE_URL=http://<autodl-host>:8000/v1
    MODEL_API_KEY=<key>
    MODEL_NAME=<served-model-name>
业务代码无需改动。
"""

from __future__ import annotations

import functools

from ..core.config import get_settings
from .base import ModelGateway
from .remote import RemoteModelGateway
from .stub import StubModelGateway


def build_gateway(provider: str | None = None) -> ModelGateway:
    settings = get_settings()
    provider = (provider or settings.model_provider or "stub").strip().lower()
    if provider in {"stub", "local", "deterministic"}:
        return StubModelGateway()
    if provider in {"openai_compatible", "openai", "vllm", "remote"}:
        return RemoteModelGateway(
            base_url=settings.model_base_url,
            api_key=settings.model_api_key,
            model=settings.model_name,
            embedding_model=settings.embedding_model,
            timeout=settings.model_timeout_secs,
            max_retries=settings.model_max_retries,
        )
    raise ValueError(f"unknown MODEL_PROVIDER: {provider}")


@functools.lru_cache(maxsize=1)
def get_gateway() -> ModelGateway:
    return build_gateway()


def reset_gateway() -> None:
    """测试用：清空单例。"""
    get_gateway.cache_clear()
