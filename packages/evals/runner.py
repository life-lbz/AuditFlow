"""评测执行器（文档 §19：数据集 / 指标 / 对照实验）。

用法：
    python -m packages.evals.runner --suite auditflow-v1 --limit 20
    python -m packages.evals.runner --compare          # 跑 4 组对照实验
    python -m packages.evals.runner --out report.json
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..core.models import (
    Approval,
    Case,
    Check,
    Decision,
    Document,
    DocumentPage,
    ErpTransaction,
    EvalCase,
    EvalRun,
    Evidence,
    ExtractedField,
    ToolCall,
    TraceEvent,
)
from ..core.db import SessionLocal
from ..documents.service import ingest_document
from ..gateway.base import ModelGateway
from ..gateway.factory import get_gateway
from ..orchestrator.orchestrator import (
    AGENT_MODE_AGENT,
    AGENT_MODE_AGENT_RESEARCHER,
    AGENT_MODE_AGENT_VERIFIER,
    AGENT_MODE_FIXED,
    CaseOrchestrator,
)
from .metrics import CaseOutcome, SuiteMetrics, compute_suite_metrics

CASE_ID_PREFIX = "evl_"


def _dataset_hash(eval_cases: list[EvalCase]) -> str:
    """对参与评测的用例做稳定指纹，用于确认报告可复现。"""
    payload = [
        {
            "id": c.id,
            "expected": c.expected_json or {},
            "tags": sorted(c.tags or []),
            "input": c.input_json or {},
        }
        for c in sorted(eval_cases, key=lambda c: c.id)
    ]
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass
class EvalReport:
    suite: str
    mode: str
    metrics: SuiteMetrics
    outcomes: list[CaseOutcome] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    duration_secs: float = 0.0
    errors: list[str] = field(default_factory=list)
    # 数据集指纹：同 hash 同配置应产出可复现的指标
    dataset_hash: str = ""

    def as_dict(self) -> dict:
        return {
            "suite": self.suite,
            "mode": self.mode,
            "dataset_hash": self.dataset_hash,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_secs": round(self.duration_secs, 2),
            "metrics": self.metrics.as_dict(),
            "errors": self.errors,
            "outcomes": [
                {
                    "case_id": o.case_id,
                    "expected": o.expected_decision,
                    "actual": o.actual_decision,
                    "effective": o.effective_decision,
                    "expected_risk": o.expected_risk,
                    "actual_risk": o.actual_risk,
                    "status": o.status,
                    "agent_used": o.agent_used,
                    "tool_calls": o.tool_calls,
                    "latency_ms": round(o.latency_ms, 1),
                    "auto_submitted": o.auto_submitted,
                    "verifier": o.verifier_status,
                    "tags": o.tags,
                }
                for o in self.outcomes
            ],
        }


class EvalRunner:
    def __init__(self, session: AsyncSession, gateway: ModelGateway | None = None) -> None:
        self.session = session
        self.gateway = gateway or get_gateway()
        self.settings = get_settings()

    async def run(
        self,
        *,
        suite: str = "auditflow-v1",
        mode: str = AGENT_MODE_AGENT_VERIFIER,
        limit: int | None = None,
        auto_submit: bool = False,
        tags: list[str] | None = None,
        reset_cases: bool = True,
    ) -> EvalReport:
        started = datetime.now(timezone.utc)
        t0 = time.perf_counter()

        eval_cases = (
            await self.session.execute(select(EvalCase).where(EvalCase.suite == suite).order_by(EvalCase.id))
        ).scalars().all()
        if not eval_cases:
            return EvalReport(
                suite=suite,
                mode=mode,
                metrics=SuiteMetrics(),
                errors=[f"评测集 {suite} 为空，请先运行 python -m infra.seed.run evals"],
            )

        if tags:
            eval_cases = [c for c in eval_cases if set(tags) & set(c.tags or [])]
        if limit:
            eval_cases = eval_cases[:limit]

        if reset_cases:
            await self._cleanup()

        dataset_hash = _dataset_hash(eval_cases)

        outcomes: list[CaseOutcome] = []
        errors: list[str] = []
        for eval_case in eval_cases:
            try:
                outcome = await self._run_single(eval_case, mode=mode, auto_submit=auto_submit)
                outcomes.append(outcome)
            except Exception as exc:  # noqa: BLE001 - 单条失败不影响整体评测
                errors.append(f"{eval_case.id}: {exc}")

        metrics = compute_suite_metrics(outcomes)
        finished = datetime.now(timezone.utc)
        report = EvalReport(
            suite=suite,
            mode=mode,
            metrics=metrics,
            outcomes=outcomes,
            started_at=started.isoformat(),
            finished_at=finished.isoformat(),
            duration_secs=time.perf_counter() - t0,
            errors=errors,
            dataset_hash=dataset_hash,
        )

        self.session.add(
            EvalRun(
                id=f"run_{int(started.timestamp())}_{mode[:12]}",
                suite=suite,
                agent_mode=mode,
                metrics_json=report.metrics.as_dict(),
            )
        )
        await self.session.commit()
        return report

    # ------------------------------------------------------------------
    async def _cleanup(self) -> None:
        """清理上一轮评测产生的案件（只删评测前缀，不动演示数据）。

        子表没有配置 ORM 级联，这里按依赖顺序显式删除，避免外键冲突。
        """

        case_ids = (
            await self.session.execute(select(Case.id).where(Case.id.like(f"{CASE_ID_PREFIX}%")))
        ).scalars().all()
        if not case_ids:
            return

        doc_ids_subq = select(Document.id).where(Document.case_id.in_(case_ids))
        await self.session.execute(
            delete(ExtractedField).where(ExtractedField.document_id.in_(doc_ids_subq))
        )
        await self.session.execute(
            delete(DocumentPage).where(DocumentPage.document_id.in_(doc_ids_subq))
        )
        await self.session.execute(delete(Document).where(Document.case_id.in_(case_ids)))
        for model in (Check, Decision, Evidence, Approval, ToolCall, TraceEvent, ErpTransaction):
            await self.session.execute(delete(model).where(model.case_id.in_(case_ids)))
        await self.session.execute(delete(Case).where(Case.id.in_(case_ids)))
        await self.session.commit()

    async def _run_single(
        self,
        eval_case: EvalCase,
        *,
        mode: str,
        auto_submit: bool,
    ) -> CaseOutcome:
        spec = eval_case.input_json or {}
        expected = eval_case.expected_json or {}
        case_id = f"{CASE_ID_PREFIX}{eval_case.id}"

        self.session.add(
            Case(
                id=case_id,
                employee_id=spec.get("employee_id", "u_0000"),
                category=spec.get("category", "travel"),
                amount=float(spec.get("amount") or 0),
                description=spec.get("description", ""),
                trace_id=f"trace_{case_id}",
            )
        )
        await self.session.flush()

        fixture_dir = self.settings.fixtures_dir / str(
            (spec.get("documents") or [{}])[0].get("fixture_dir") or eval_case.id
        )
        for doc in spec.get("documents") or []:
            path: Path = fixture_dir / doc.get("filename", "")
            if not path.exists():
                continue
            await ingest_document(
                self.session,
                self.gateway,
                case_id=case_id,
                filename=doc["filename"],
                data=path.read_bytes(),
                declared_type=doc.get("type"),
                trace_id=f"trace_{case_id}",
            )
        await self.session.commit()

        t0 = time.perf_counter()
        orchestrator = CaseOrchestrator(self.session, self.gateway, mode=mode)
        result = await orchestrator.run(case_id, auto_submit=auto_submit)
        latency_ms = (time.perf_counter() - t0) * 1000

        outcome = CaseOutcome(
            case_id=case_id,
            expected_decision=expected.get("decision", ""),
            actual_decision=result.decision.get("decision", ""),
            expected_risk=expected.get("risk_level", ""),
            actual_risk=result.decision.get("risk_level", ""),
            category=spec.get("category", ""),
            status=result.status,
            agent_used=result.agent_used,
            tool_calls=result.tool_calls,
            latency_ms=latency_ms,
            auto_submitted=(result.erp or {}).get("status") in {"confirmed", "duplicate_prevented"},
            needs_approval=result.needs_approval,
            erp_status=(result.erp or {}).get("status", ""),
            verifier_status=result.verifier_status,
            field_truth=expected.get("fields") or {},
            field_extracted=await self._extracted_invoice_fields(case_id),
            retrieved_policy_ids=await self._retrieved_policy_ids(case_id),
            decision_evidence_ids=await self._decision_evidence_ids(case_id),
            known_evidence_ids=await self._known_evidence_ids(case_id),
            tags=list(eval_case.tags or []),
            errors=list(result.errors),
        )
        return outcome

    # ------------------------------------------------------------------
    async def _extracted_invoice_fields(self, case_id: str) -> dict:
        rows = (
            await self.session.execute(
                select(ExtractedField)
                .join(Document, ExtractedField.document_id == Document.id)
                .where(Document.case_id == case_id, Document.type == "invoice")
            )
        ).scalars().all()
        fields: dict = {}
        for row in rows:
            fields.setdefault(row.field_name, _coerce(row.value))
        return fields

    async def _retrieved_policy_ids(self, case_id: str) -> list[str]:
        """从 Trace 的 policy_retrieved 事件还原检索顺序（Recall@k 需要保序）。"""
        events = (
            await self.session.execute(
                select(TraceEvent)
                .where(TraceEvent.case_id == case_id, TraceEvent.event_type == "policy_retrieved")
                .order_by(TraceEvent.created_at)
            )
        ).scalars().all()
        ordered: list[str] = []
        for event in events:
            for hit in (event.payload_json or {}).get("hits", []):
                pid = hit.get("policy_id")
                if pid and pid not in ordered:
                    ordered.append(pid)
        return ordered

    async def _decision_evidence_ids(self, case_id: str) -> list[str]:
        row = (
            await self.session.execute(
                select(Decision)
                .where(Decision.case_id == case_id)
                .order_by(Decision.created_at.desc())
            )
        ).scalars().first()
        return list(row.evidence_ids or []) if row else []

    async def _known_evidence_ids(self, case_id: str) -> list[str]:
        rows = (
            await self.session.execute(select(Evidence.id).where(Evidence.case_id == case_id))
        ).scalars().all()
        return list(rows)


def _coerce(raw: str):
    text = str(raw).strip()
    try:
        if "." in text:
            return float(text)
        if text.isdigit():
            return int(text)
    except ValueError:
        pass
    return text


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

COMPARE_MODES = (
    AGENT_MODE_FIXED,
    AGENT_MODE_AGENT,
    AGENT_MODE_AGENT_VERIFIER,
    AGENT_MODE_AGENT_RESEARCHER,
)


async def _main_async(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="AuditFlow 评测")
    parser.add_argument("--suite", default="auditflow-v1")
    parser.add_argument("--mode", default=AGENT_MODE_AGENT_VERIFIER, choices=COMPARE_MODES)
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 条")
    parser.add_argument("--tags", default=None, help="逗号分隔的标签过滤")
    parser.add_argument("--auto-submit", action="store_true", help="允许低风险案件自动提交 ERP")
    parser.add_argument("--compare", action="store_true", help="依次跑 4 组对照实验")
    parser.add_argument("--out", default=None, help="输出 JSON 报告路径")
    args = parser.parse_args(argv)

    tag_filter = [t.strip() for t in args.tags.split(",")] if args.tags else None
    gateway = get_gateway()

    async with SessionLocal() as session:
        reports: dict[str, dict] = {}
        modes = COMPARE_MODES if args.compare else (args.mode,)
        for mode in modes:
            runner = EvalRunner(session, gateway)
            report = await runner.run(
                suite=args.suite,
                mode=mode,
                limit=args.limit,
                auto_submit=args.auto_submit,
                tags=tag_filter,
            )
            m = report.metrics
            ci = m.decision_accuracy_ci95
            print(
                f"[{mode}] n={m.total} hash={report.dataset_hash} "
                f"决策准确率={m.decision_accuracy:.3f}"
                f"{f' CI95=[{ci[0]:.3f},{ci[1]:.3f}]' if ci else ''} "
                f"风险准确率={m.risk_accuracy:.3f} "
                f"错误自动通过率={m.error_auto_approve_rate:.3f} "
                f"错误放行率={m.error_release_rate:.3f} "
                f"Verifier拦截率={m.verifier_interception_rate:.3f} "
                f"拦截后仍提交={m.post_verifier_false_approve} "
                f"人工接管率={m.human_takeover_rate:.3f} "
                f"字段F1={m.field_f1:.3f} "
                f"制度Recall@5={m.policy_recall_at_5:.3f} "
                f"证据覆盖={m.evidence_coverage:.3f} "
                f"平均步数={m.avg_tool_steps:.2f} "
                f"P95={m.p95_latency_ms:.0f}ms "
                f"耗时={report.duration_secs:.1f}s"
            )
            for group, stats in m.by_group.items():
                print(
                    f"    [{group}] n={stats['count']} "
                    f"决策准确率={stats['decision_accuracy']:.3f} "
                    f"错误自动通过率={stats['error_auto_approve_rate']:.3f} "
                    f"错误放行率={stats['error_release_rate']:.3f} "
                    f"拦截率={stats['verifier_interception_rate']:.3f}"
                )
            if report.errors:
                print(f"  错误 {len(report.errors)} 条：{report.errors[:3]}")
            reports[mode] = report.as_dict()

        if args.out:
            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            payload = reports if args.compare else next(iter(reports.values()))
            out_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"[eval] 报告已写入 {out_path}")

    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_main_async(sys.argv[1:])))


if __name__ == "__main__":
    main()
