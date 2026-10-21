"""种子数据 CLI：生成制度、票据与评测案件并入库。

用法：
    python -m infra.seed.run all          # 制度落盘+入库、生成票据、写入评测集
    python -m infra.seed.run policies     # 只处理制度
    python -m infra.seed.run fixtures     # 只生成票据图片/文本
    python -m infra.seed.run evals        # 只写入 120 条评测案件
    python -m infra.seed.run demo-case    # 基于 fixtures 创建一个可跑的真实案件
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from sqlalchemy import delete

from packages.core.models import Case, EvalCase
from packages.core.config import get_settings
from packages.core.db import SessionLocal
from packages.documents.service import ingest_document
from packages.gateway.factory import get_gateway
from packages.retrieval.indexer import PolicyDocument, index_policy

from .generator import (
    CaseSpec,
    generate_cases,
    write_fixtures,
    write_manifest,
    write_policies,
    write_vendors,
)
from .policies_data import ALL_POLICIES


async def seed_policies(*, index_db: bool = True) -> int:
    settings = get_settings()
    policies = write_policies(settings.policies_dir)
    print(f"[policies] 已生成 {len(policies)} 份制度到 {settings.policies_dir}")

    if not index_db:
        return len(policies)

    gateway = get_gateway()
    total_chunks = 0
    async with SessionLocal() as session:
        for policy in ALL_POLICIES:
            doc = PolicyDocument(
                policy_id=policy.policy_id,
                title=policy.title,
                version=policy.version,
                effective_date=policy.effective_date,
                category=policy.category,
                markdown=policy.markdown,
            )
            count = await index_policy(session, gateway, doc, replace=True)
            total_chunks += count
            print(f"[policies] {policy.policy_id} v{policy.version} → {count} 个切片")
        await session.commit()
    print(f"[policies] 共入库 {total_chunks} 个切片")
    return len(policies)


def seed_fixtures(*, with_png: bool = True, seed: int = 42) -> list[CaseSpec]:
    settings = get_settings()
    cases = generate_cases(seed=seed)
    written = write_fixtures(settings.fixtures_dir, cases, with_png=with_png)
    manifest = write_manifest(settings.fixtures_dir, cases)
    write_vendors(settings.fixtures_dir.parent / "vendors.json")
    print(f"[fixtures] {len(cases)} 个案件，{written} 份文档 → {settings.fixtures_dir}")
    print(f"[fixtures] 清单：{manifest}")
    return cases


async def seed_eval_cases(cases: list[CaseSpec] | None = None) -> int:
    cases = cases or generate_cases()
    async with SessionLocal() as session:
        await session.execute(delete(EvalCase))
        for case in cases:
            session.add(
                EvalCase(
                    id=case.case_id,
                    suite="auditflow-v1",
                    input_json={
                        "employee_id": case.employee_id,
                        "category": case.category,
                        "amount": case.amount,
                        "description": case.description,
                        "documents": [
                            {
                                "type": d.doc_type,
                                "filename": d.filename,
                                "fixture_dir": case.case_id,
                            }
                            for d in case.docs
                        ],
                    },
                    expected_json={
                        "decision": case.expected_decision,
                        "risk_level": case.expected_risk,
                        "fields": case.field_truth,
                    },
                    tags=case.tags,
                )
            )
        await session.commit()
    print(f"[evals] 已写入 {len(cases)} 条评测案件（suite=auditflow-v1）")
    return len(cases)


async def create_demo_case(case_id: str | None = None, *, keep_open: bool = False) -> str:
    """基于 fixtures 创建一个真实案件并上传文档，用于手动演示。"""
    settings = get_settings()
    manifest_path = settings.fixtures_dir / "manifest.json"
    if not manifest_path.exists():
        seed_fixtures()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    spec = next(
        (c for c in manifest["cases"] if case_id is None or c["case_id"] == case_id),
        None,
    )
    if spec is None:
        raise SystemExit(f"未找到案件 {case_id}")

    target_id = case_id or spec["case_id"]
    gateway = get_gateway()

    async with SessionLocal() as session:
        existing = await session.get(Case, target_id)
        if existing is None:
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

        case_dir = settings.fixtures_dir / spec["case_id"]
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
            )
        await session.commit()

    print(f"[demo] 案件 {target_id} 已创建，文档 {len(spec['documents'])} 份")
    return target_id


async def main_async(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="AuditFlow 种子数据")
    parser.add_argument(
        "command",
        choices=["all", "policies", "fixtures", "evals", "demo-case"],
        help="要执行的步骤",
    )
    parser.add_argument("--case-id", default=None, help="demo-case 时指定案件 ID")
    parser.add_argument("--no-png", action="store_true", help="不生成票据图片")
    parser.add_argument("--no-db", action="store_true", help="只落盘，不写数据库")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    args = parser.parse_args(argv)

    if args.command in {"all", "policies"}:
        await seed_policies(index_db=not args.no_db)
    if args.command in {"all", "fixtures"}:
        seed_fixtures(with_png=not args.no_png, seed=args.seed)
    if args.command in {"all", "evals"}:
        if args.no_db:
            print("[evals] --no-db 已跳过写库")
        else:
            await seed_eval_cases()
    if args.command == "demo-case":
        await create_demo_case(args.case_id)
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async(sys.argv[1:])))


if __name__ == "__main__":
    main()
