"""测试公共夹具：SQLite 隔离数据库、制度索引、合成数据案件加载。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# 测试默认使用本地文件存储与 stub 模型网关，避免依赖 MinIO / GPU
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("MODEL_PROVIDER", "stub")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
# Mock ERP 走进程内 ASGI，测试无需启动 Docker 容器
os.environ.setdefault("MOCK_ERP_INPROCESS", "true")

from packages.core.models import Base, Case  # noqa: E402
from packages.documents.service import ingest_document  # noqa: E402
from packages.gateway.factory import get_gateway  # noqa: E402
from packages.retrieval.indexer import PolicyDocument, index_policy  # noqa: E402
from infra.seed.policies_data import ALL_POLICIES  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "data" / "fixtures"


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session(db_engine) -> AsyncSession:
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s


@pytest.fixture
def gateway():
    return get_gateway()


@pytest_asyncio.fixture
async def indexed_policies(session: AsyncSession, gateway):
    """把 5 份制度切分并向量化入库。"""
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
    return session


def load_manifest() -> dict:
    path = FIXTURES_DIR / "manifest.json"
    if not path.exists():
        pytest.skip(f"合成数据未生成：{path}（请先运行 python -m infra.seed.run fixtures）")
    return json.loads(path.read_text(encoding="utf-8"))


def find_case_spec(tags: list[str] | str, *, category: str | None = None) -> dict:
    """按标签查找一条合成案件定义。"""
    manifest = load_manifest()
    wanted = [tags] if isinstance(tags, str) else tags
    for case in manifest["cases"]:
        if category and case["category"] != category:
            continue
        if all(t in case["tags"] for t in wanted):
            return case
    pytest.skip(f"manifest 中未找到标签 {wanted} 的案件")


async def create_case_from_spec(
    session: AsyncSession,
    gateway,
    spec: dict,
    *,
    case_id: str | None = None,
) -> str:
    """按案件定义创建 Case 并上传其全部文档。"""
    target_id = case_id or f"test_{spec['case_id']}"
    session.add(
        Case(
            id=target_id,
            employee_id=spec["employee_id"],
            category=spec["category"],
            amount=float(spec["amount"]),
            description=spec["description"],
            trace_id=f"trace_{target_id}",
        )
    )
    await session.flush()

    case_dir = FIXTURES_DIR / spec["case_id"]
    for doc in spec["documents"]:
        txt_path = case_dir / doc["filename"]
        if not txt_path.exists():
            continue
        await ingest_document(
            session,
            gateway,
            case_id=target_id,
            filename=doc["filename"],
            data=txt_path.read_bytes(),
            declared_type=doc["type"],
            trace_id=f"trace_{target_id}",
        )
    await session.commit()
    return target_id
