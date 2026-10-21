"""AuditFlow 统一配置（文档 §26.2：配置通过环境变量注入，不提交密钥）。"""

from __future__ import annotations

import functools
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """全局配置。所有字段均可通过环境变量覆盖。"""

    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 基础设施 ----
    database_url: str = "postgresql+asyncpg://auditflow:auditflow@localhost:5433/auditflow"
    redis_url: str = "redis://localhost:6379/0"
    trace_store_url: str = ""

    # ---- MinIO ----
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "auditflow"
    minio_secret_key: str = "auditflow123"
    minio_bucket: str = "auditflow-docs"
    minio_secure: bool = False

    # ---- 存储后端：minio / local ----
    storage_backend: str = "minio"

    # ---- Mock ERP ----
    mock_erp_url: str = "http://localhost:8010"
    # http：访问真实 Mock ERP 服务；inprocess：进程内 ASGI（测试 / 离线演示，无需 Docker）
    mock_erp_inprocess: bool = False

    # ---- API ----
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173"
    max_upload_mb: int = 20
    max_documents_per_case: int = 20

    # ---- 鉴权 ----
    # dev：信任 X-Role / X-User-Id 请求头（仅本地演示）；token：HMAC 签名的 Bearer Token
    auth_mode: str = "dev"
    auth_secret: str = "auditflow-dev-secret-change-me"
    auth_token_ttl_secs: int = 3600

    # ---- 模型网关（文档 §17）----
    # stub：本地确定性实现，全流程可跑通；openai_compatible：AutoDL/vLLM 等 OpenAI 兼容服务
    model_provider: str = "stub"
    model_api_key: str = ""
    model_base_url: str = ""
    model_name: str = "qwen2.5-7b-instruct"
    vision_model_name: str = ""
    embedding_model: str = "bge-m3"
    reranker_model: str = "bge-reranker-v2-m3"
    model_timeout_secs: float = 30.0
    model_max_retries: int = 2

    # ---- Agent 预算（文档 §10.4）----
    agent_max_tool_calls: int = 8
    agent_max_replans: int = 1
    agent_retries_per_tool: int = 2
    agent_max_steps: int = 12

    # ---- 超时（文档 §21.2，单位秒）----
    ocr_timeout_secs: float = 60.0
    tool_timeout_secs: float = 10.0
    case_timeout_secs: float = 90.0

    # ---- 检索参数（文档 §8.3）----
    retrieval_keyword_k: int = 10
    retrieval_vector_k: int = 10
    retrieval_fusion_k: int = 12
    retrieval_rerank_k: int = 5

    # ---- 风险阈值（文档 §2.3）----
    risk_low_threshold: float = 0.30
    risk_high_threshold: float = 0.60

    # ---- 路径 ----
    data_dir: Path = PROJECT_ROOT / "data"
    local_storage_dir: Path = PROJECT_ROOT / "data" / "_storage"

    @property
    def policies_dir(self) -> Path:
        return self.data_dir / "policies"

    @property
    def fixtures_dir(self) -> Path:
        return self.data_dir / "fixtures"

    @property
    def synthetic_dir(self) -> Path:
        return self.data_dir / "synthetic"

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
