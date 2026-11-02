"""AuditFlow 统一配置（文档 §26.2：配置通过环境变量注入，不提交密钥）。"""

from __future__ import annotations

import functools
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 出厂默认值：生产环境一旦检测到仍然使用这些值，直接拒绝启动
DEFAULT_AUTH_SECRET = "auditflow-dev-secret-change-me"
DEFAULT_MINIO_SECRET = "auditflow123"


class Settings(BaseSettings):
    """全局配置。所有字段均可通过环境变量覆盖。"""

    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 运行环境 ----
    # development / staging / production；production 会启用安全基线自检
    environment: str = "development"
    app_version: str = "0.1.0"

    # ---- 日志 ----
    log_level: str = "INFO"
    log_format: str = "text"  # text / json（生产建议 json，便于日志采集）

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
    # 单个 HTTP 请求体上限（multipart 上传含边界开销，略大于 max_upload_mb）
    max_request_mb: int = 25
    # 生产建议关闭 /docs、/redoc 与 /openapi.json，减少暴露面
    docs_enabled: bool = True
    security_headers_enabled: bool = True

    # ---- 鉴权 ----
    # dev：信任 X-Role / X-User-Id 请求头（仅本地演示）；token：HMAC 签名的 Bearer Token
    auth_mode: str = "dev"
    auth_secret: str = DEFAULT_AUTH_SECRET
    auth_token_ttl_secs: int = 3600
    # 非空时 /metrics 需要 Authorization: Bearer <METRICS_TOKEN>
    metrics_token: str = ""

    # ---- 限流与并发 ----
    # 未显式开启时，production 环境自动启用；development 默认关闭便于压测
    rate_limit_enabled: bool = False
    rate_limit_requests_per_minute: int = 120
    rate_limit_writes_per_minute: int = 30
    rate_limit_backend: str = "memory"  # memory / redis（redis 不可用时自动降级为 memory）
    case_lock_backend: str = "memory"  # memory / redis
    redis_timeout_secs: float = 1.0
    # 是否信任反向代理注入的 X-Real-IP（仅当 API 不直接对外暴露时开启）
    trust_proxy_headers: bool = False

    # ---- 数据库连接池 ----
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle_secs: int = 1800
    db_pool_timeout_secs: float = 30.0
    db_connect_timeout_secs: float = 10.0

    # ---- 健康检查 ----
    readiness_timeout_secs: float = 3.0

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

    @property
    def is_production(self) -> bool:
        return (self.environment or "development").strip().lower() == "production"

    @property
    def rate_limit_active(self) -> bool:
        """生产环境默认启用限流；开发环境需显式打开。"""
        return self.rate_limit_enabled or self.is_production

    @property
    def max_request_bytes(self) -> int:
        # 请求体上限至少覆盖单文件上传上限；multipart 边界开销可忽略
        return max(self.max_request_mb, self.max_upload_mb) * 1024 * 1024

    def production_errors(self) -> list[str]:
        """生产环境启动硬性阻断项：任何一条命中都不能启动。"""
        if not self.is_production:
            return []
        errors: list[str] = []
        if (self.auth_mode or "dev").strip().lower() != "token":
            errors.append("AUTH_MODE 必须为 token（dev 模式会信任可伪造的请求头）")
        if self.auth_secret in ("", DEFAULT_AUTH_SECRET) or len(self.auth_secret) < 32:
            errors.append("AUTH_SECRET 必须替换为随机值且长度不少于 32 个字符")
        if self.cors_origin_list == ["*"]:
            errors.append("CORS_ORIGINS 不得为 *，请填写具体前端域名")
        return errors

    def production_warnings(self) -> list[str]:
        """生产环境可用但应关注的风险项，会在启动日志与 /health/ready 中暴露。"""
        if not self.is_production:
            return []
        warnings: list[str] = []
        if self.storage_backend.strip().lower() == "local":
            warnings.append("STORAGE_BACKEND=local，多副本部署会丢失证据文件，建议使用 minio")
        if self.minio_secret_key in ("", DEFAULT_MINIO_SECRET):
            warnings.append("MINIO_SECRET_KEY 仍为默认值")
        if self.model_provider.strip().lower() == "stub":
            warnings.append("MODEL_PROVIDER=stub：审核结论由确定性启发式产生，未接入真实模型")
        if self.mock_erp_inprocess:
            warnings.append("MOCK_ERP_INPROCESS=true：ERP 为进程内模拟，未连接真实系统")
        if not self.rate_limit_active:
            warnings.append("限流未启用")
        if self.docs_enabled:
            warnings.append("DOCS_ENABLED=true：/docs 与 /openapi.json 对外可见")
        return warnings


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
