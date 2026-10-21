"""Case Orchestrator：确定性工作流编排（文档 §4.1、§5、ADR-001）。

核心主张：
- 正常流程由确定性状态机驱动，不消耗模型调用。
- 只有异常路径才进入 Decision Agent（文档 §5.4 触发条件）。
- 所有结论都必须经过 Verifier；高风险写入必须经 Action Gateway。

编排步骤与状态机严格对应：
    CREATED → INGESTED → EXTRACTED → EVIDENCE_READY → VALIDATED
            → DECIDED → VERIFIED → {WAITING_APPROVAL | EXECUTING | NEEDS_INFO | REJECTED}
            → EXECUTING → COMPLETED
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent.runtime import DecisionAgentRuntime, make_tool_context
from ..agent.schemas import AgentState, DecisionPayload
from ..core.config import PROJECT_ROOT, get_settings
from ..core.models import (
    Case,
    CaseStatus,
    Decision,
    Document,
    Evidence,
    ExtractedField,
    Check,
)
from ..core.state_machine import (
    CaseEvent,
    GuardFailedError,
    OptimisticLockError,
    TransitionError,
    transition,
)
from ..core.tracing import (
    EVENT_CASE_COMPLETED,
    EVENT_DECISION_MADE,
    EVENT_POLICY_RETRIEVED,
    EVENT_VERIFIER_FINISHED,
    emit_trace,
    new_id,
)
from ..documents.extractor import load_case_fields
from ..documents.schema import DOCUMENT_TYPE_LABELS, FIELD_LABELS, REQUIRED_DOCUMENTS
from ..erp.gateway import ActionGateway
from ..gateway.base import DocumentType, ModelGateway
from ..gateway.factory import get_gateway
from ..retrieval.hybrid_search import HybridSearcher, SearchHit
from ..rules.engine import RuleContext, RuleEngine, RuleRunResult
from ..rules.limits import extract_category_cap, extract_limits
from ..tools.registry import ToolRegistry
from ..verifier.model_verifier import TwoLayerVerifier

# 实验组（文档 §19.3）
AGENT_MODE_FIXED = "fixed_workflow"          # A：固定工作流，不使用 Agent
AGENT_MODE_AGENT = "single_agent"            # B：单 Agent 处理异常（无 Verifier）
AGENT_MODE_AGENT_VERIFIER = "single_agent_verifier"  # C：单 Agent + Verifier（默认）
AGENT_MODE_AGENT_RESEARCHER = "single_agent_verifier_researcher"  # D：C + 只读 Policy Researcher

AGENT_MODES = (
    AGENT_MODE_FIXED,
    AGENT_MODE_AGENT,
    AGENT_MODE_AGENT_VERIFIER,
    AGENT_MODE_AGENT_RESEARCHER,
)

# Agent 触发原因（文档 §5.4）
TRIGGER_MISSING_DOCS = "missing_documents"
TRIGGER_LOW_CONFIDENCE = "low_extraction_confidence"
TRIGGER_AMOUNT_MISMATCH = "amount_mismatch"
TRIGGER_DUPLICATE = "duplicate_suspicion"
TRIGGER_POLICY_CONFLICT = "policy_conflict"
TRIGGER_NO_POLICY = "no_policy_found"
TRIGGER_RULE_FAILED = "rule_failed"


@dataclass
class OrchestrationResult:
    case_id: str
    status: str = ""
    mode: str = AGENT_MODE_AGENT_VERIFIER
    decision: dict[str, Any] = field(default_factory=dict)
    risk_level: str = ""
    risk_score: float = 0.0
    agent_used: bool = False
    agent_triggers: list[str] = field(default_factory=list)
    agent_steps: int = 0
    tool_calls: int = 0
    rule_failures: list[str] = field(default_factory=list)
    evidence_count: int = 0
    verifier_status: str = ""
    verifier_reason: str = ""
    needs_approval: bool = False
    auto_submit_eligible: bool = False
    erp: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)
    transitions: list[str] = field(default_factory=list)


class CaseOrchestrator:
    def __init__(
        self,
        session: AsyncSession,
        gateway: ModelGateway | None = None,
        *,
        mode: str = AGENT_MODE_AGENT_VERIFIER,
        actor: str = "orchestrator",
    ) -> None:
        if mode not in AGENT_MODES:
            raise ValueError(f"未知实验模式 {mode}，可选：{AGENT_MODES}")
        self.session = session
        self.gateway = gateway or get_gateway()
        self.mode = mode
        self.actor = actor
        self.settings = get_settings()
        self.rule_engine = RuleEngine()
        self.searcher = HybridSearcher(self.gateway)
        self.registry = ToolRegistry()
        self.agent = DecisionAgentRuntime(self.gateway, self.registry)
        self.verifier = TwoLayerVerifier(self.gateway)

    # ------------------------------------------------------------------
    async def run(self, case_id: str, *, auto_submit: bool = True) -> OrchestrationResult:
        result = OrchestrationResult(case_id=case_id, mode=self.mode)
        case = await self.session.get(Case, case_id)
        if case is None:
            result.errors.append(f"案件不存在: {case_id}")
            return result

        trace_id = case.trace_id or f"trace_{case_id}"
        try:
            await self._advance(case, CaseEvent.DOCUMENTS_UPLOADED, result, trace_id)
            case = await self._reload(case_id)
            if case.status == CaseStatus.CREATED.value:
                result.errors.append("案件没有文档，无法进入 INGESTED")
                result.status = case.status
                return result

            await self._advance(case, CaseEvent.EXTRACT_STARTED, result, trace_id)
            case = await self._reload(case_id)

            # 检索制度 → EVIDENCE_READY
            ctx, hits = await self._build_rule_context(case, trace_id, result)
            await self._advance(case, CaseEvent.POLICY_SEARCH_COMPLETED, result, trace_id)
            case = await self._reload(case_id)

            # 规则 → VALIDATED
            rule_outcome = await self.rule_engine.run_and_persist(self.session, ctx, trace_id=trace_id)
            result.rule_failures = rule_outcome.failed_names
            result.risk_level = rule_outcome.risk.level
            result.risk_score = rule_outcome.risk.score
            case.risk_level = rule_outcome.risk.level
            await self.session.flush()
            await self._advance(case, CaseEvent.RULES_COMPLETED, result, trace_id)
            case = await self._reload(case_id)

            # 决策 → DECIDED
            decision, triggers = await self._make_decision(
                case, ctx, rule_outcome, hits, trace_id, result
            )
            result.decision = decision.as_dict()
            result.risk_level = decision.risk_level
            result.agent_used = bool(triggers)
            result.agent_triggers = triggers
            await self._persist_decision(case, decision, result, trace_id)
            await self._advance(
                case,
                CaseEvent.DECISION_GENERATED,
                result,
                trace_id,
                context={"decision": decision.as_dict()},
            )
            case = await self._reload(case_id)

            # 验证 → VERIFIED
            verification = await self._verify(case, decision, ctx, rule_outcome, trace_id, result)
            result.verifier_status = verification.status
            result.verifier_reason = verification.reason
            if verification.status != "passed":
                await self._advance(
                    case, CaseEvent.FATAL_ERROR, result, trace_id, context={"verifier": verification.reason}
                )
                result.status = CaseStatus.FAILED.value
                return result
            await self._advance(
                case, CaseEvent.VERIFICATION_PASSED, result, trace_id, context={"verifier": verification.reason}
            )
            case = await self._reload(case_id)

            # 按决策分流
            if decision.decision == "need_info":
                await self._advance(case, CaseEvent.EVIDENCE_MISSING, result, trace_id)
                result.needs_approval = False
                result.status = CaseStatus.NEEDS_INFO.value
                return result

            if decision.decision == "reject":
                await self._advance(case, CaseEvent.REJECTED, result, trace_id)
                result.status = CaseStatus.REJECTED.value
                return result

            auto_allowed = (
                decision.decision == "approve"
                and decision.risk_level == "low"
                and not rule_outcome.failed
            )
            result.auto_submit_eligible = auto_allowed
            if auto_allowed and auto_submit:
                await self._advance(
                    case,
                    CaseEvent.AUTO_SUBMIT_ALLOWED,
                    result,
                    trace_id,
                    context={"auto_submit_allowed": True},
                )
                case = await self._reload(case_id)
                await self._execute_erp(case, decision, result, trace_id, approved_by=None)
                return result

            await self._advance(case, CaseEvent.APPROVAL_REQUIRED, result, trace_id)
            result.needs_approval = True
            result.status = CaseStatus.WAITING_APPROVAL.value
            return result

        except (TransitionError, GuardFailedError, OptimisticLockError) as exc:
            result.errors.append(f"状态迁移失败: {exc}")
            result.status = (await self._reload(case_id)).status
            return result
        finally:
            await self.session.commit()

    # ------------------------------------------------------------------
    # 审批后继续执行
    # ------------------------------------------------------------------
    async def execute_after_approval(
        self,
        case_id: str,
        *,
        approved_by: str,
        auto_submit: bool = True,
    ) -> OrchestrationResult:
        """人工批准后：EXECUTING → ERP → COMPLETED/ESCALATED。"""
        result = OrchestrationResult(case_id=case_id, mode=self.mode)
        case = await self.session.get(Case, case_id)
        if case is None:
            result.errors.append(f"案件不存在: {case_id}")
            return result
        trace_id = case.trace_id or f"trace_{case_id}"

        if case.status == CaseStatus.ESCALATED.value:
            await self._advance(
                case, CaseEvent.APPROVED, result, trace_id,
                context={"actor_role": "reviewer"}, actor=approved_by,
            )
        elif case.status == CaseStatus.WAITING_APPROVAL.value:
            await self._advance(
                case, CaseEvent.APPROVED, result, trace_id,
                context={"actor_role": "reviewer"}, actor=approved_by,
            )
        elif case.status != CaseStatus.EXECUTING.value:
            result.errors.append(f"当前状态 {case.status} 不允许执行 ERP")
            result.status = case.status
            return result

        case = await self._reload(case_id)
        if auto_submit:
            decision = await self._latest_decision(case_id)
            await self._execute_erp(case, decision, result, trace_id, approved_by=approved_by)
        else:
            result.status = case.status
        await self.session.commit()
        return result

    # ------------------------------------------------------------------
    # 内部步骤
    # ------------------------------------------------------------------
    async def _reload(self, case_id: str) -> Case:
        case = await self.session.get(Case, case_id)
        if case is None:
            raise TransitionError(f"case not found: {case_id}")
        await self.session.refresh(case)
        return case

    async def _advance(
        self,
        case: Case,
        event: CaseEvent,
        result: OrchestrationResult,
        trace_id: str,
        *,
        context: dict[str, Any] | None = None,
        actor: str | None = None,
    ) -> None:
        """执行一次状态迁移；当前状态不接受该事件时静默跳过（支持断点续跑）。"""
        current = CaseStatus(case.status)
        try:
            expected = case.state_version
            updated = await transition(
                self.session,
                case.id,
                event,
                expected_version=expected,
                actor=actor or self.actor,
                context=context,
            )
            result.transitions.append(f"{current.value} +{event.value}-> {updated.status}")
            case.state_version = updated.state_version
            case.status = updated.status
        except GuardFailedError:
            # 守卫不满足（如自动提交条件不成立）交由上层分流
            raise
        except TransitionError:
            # 该状态不处理此事件：流程继续（便于从中间状态重跑）
            return
        except OptimisticLockError:
            raise

    async def _build_rule_context(
        self,
        case: Case,
        trace_id: str,
        result: OrchestrationResult,
    ) -> tuple[RuleContext, list[SearchHit]]:
        session = self.session
        cf = await load_case_fields(session, case.id)
        docs = (
            await session.execute(select(Document).where(Document.case_id == case.id))
        ).scalars().all()
        document_types = [d.type for d in docs]
        document_ids: dict[str, str] = {}
        for d in docs:
            document_ids.setdefault(d.type, d.id)

        # 1) 制度检索（多角度查询聚合，提升召回）
        queries = self._policy_queries(case)
        all_hits: list[SearchHit] = []
        seen: set[str] = set()
        for q in queries:
            sr = await self.searcher.search(session, q, top_k=5, category=case.category)
            for hit in sr.hits:
                if hit.chunk_id in seen:
                    continue
                seen.add(hit.chunk_id)
                all_hits.append(hit)

        # 2) 证据落库
        evidence_by_field, policy_evidence = await self._persist_evidence(case, cf, docs, all_hits, trace_id)
        result.evidence_count = len(evidence_by_field) + len(policy_evidence)

        # 3) 额度解析
        chunks = [h.as_dict() for h in all_hits]
        limits = extract_limits(chunks)
        cap = extract_category_cap(chunks, case.category)
        if cap is not None:
            limits["category_cap"] = cap

        # 4) 历史案件（重复报销比对）
        history = await self._load_history(case)

        whitelist, blacklist = load_vendor_lists()

        ctx = RuleContext(
            case_id=case.id,
            employee_id=case.employee_id,
            category=case.category,
            amount=float(case.amount or 0),
            description=case.description or "",
            fields=cf.values,
            confidences=cf.confidences,
            by_document=cf.by_document,
            document_types=document_types,
            document_ids=document_ids,
            policy_chunks=chunks,
            policy_limits=limits,
            history=history,
            evidence_by_field=evidence_by_field,
            policy_evidence=policy_evidence,
            vendor_whitelist=whitelist,
            vendor_blacklist=blacklist,
        )
        # 低置信度文档交给 Agent 复检
        ctx.__dict__["_low_confidence_documents"] = cf.low_confidence_documents
        return ctx, all_hits

    def _policy_queries(self, case: Case) -> list[str]:
        base = {
            "travel": ["差旅报销 住宿 交通 额度 上限", "出差审批 要求 报销材料"],
            "meal": ["餐饮报销 人均 额度 上限", "业务招待 报销要求"],
            "office_supplies": ["办公用品 采购 单件 额度 上限", "办公用品 报销要求"],
        }.get(case.category, ["报销制度 通用 要求"])
        return [*base, "报销 必需材料 发票 流水 提交要求"]

    async def _persist_evidence(
        self,
        case: Case,
        cf,
        docs: list[Document],
        hits: list[SearchHit],
        trace_id: str,
    ) -> tuple[dict[str, str], list[Any]]:
        from ..rules.engine import EvidenceRef

        evidence_by_field: dict[str, str] = {}
        doc_by_id = {d.id: d for d in docs}

        for doc_id, fields in cf.by_document.items():
            doc = doc_by_id.get(doc_id)
            source_type = doc.type if doc else "document"
            for name, value in fields.items():
                ev_id = new_id("ev")
                quote = f"{FIELD_LABELS.get(name, name)}：{value}"
                self.session.add(
                    Evidence(
                        id=ev_id,
                        case_id=case.id,
                        source_type=source_type,
                        source_id=doc_id,
                        page_no=1,
                        bbox=None,
                        quote=quote,
                        score=cf.confidences.get(name, 0.0),
                        document_version="v1",
                    )
                )
                evidence_by_field.setdefault(name, ev_id)

        policy_evidence: list[EvidenceRef] = []
        for hit in hits:
            ev_id = new_id("ev")
            self.session.add(
                Evidence(
                    id=ev_id,
                    case_id=case.id,
                    source_type="policy",
                    source_id=hit.chunk_id,
                    page_no=hit.page_no,
                    bbox=None,
                    quote=hit.content[:400],
                    score=hit.score,
                    document_version=hit.version,
                )
            )
            policy_evidence.append(
                EvidenceRef(
                    evidence_id=ev_id,
                    source_type="policy",
                    source_id=hit.chunk_id,
                    quote=hit.content[:400],
                    page_no=hit.page_no,
                    score=hit.score,
                )
            )

        await emit_trace(
            self.session,
            case_id=case.id,
            trace_id=trace_id,
            event_type=EVENT_POLICY_RETRIEVED,
            actor="evidence_service",
            payload={
                "queries": self._policy_queries(case),
                "hits": [h.as_dict() for h in hits],
                "citations": [h.citation() for h in hits],
            },
        )
        await self.session.flush()
        return evidence_by_field, policy_evidence

    async def _load_history(self, case: Case) -> list[dict[str, Any]]:
        """加载历史案件的发票信息用于重复检测（只取已完成/待审的）。"""
        rows = (
            await self.session.execute(
                select(ExtractedField, Document, Case)
                .join(Document, ExtractedField.document_id == Document.id)
                .join(Case, Document.case_id == Case.id)
                .where(
                    Document.type == DocumentType.INVOICE.value,
                    Case.id != case.id,
                    Case.status.in_(
                        [
                            CaseStatus.COMPLETED.value,
                            CaseStatus.WAITING_APPROVAL.value,
                            CaseStatus.VERIFIED.value,
                            CaseStatus.EXECUTING.value,
                            CaseStatus.ESCALATED.value,
                        ]
                    ),
                )
            )
        ).all()

        grouped: dict[str, dict[str, Any]] = {}
        for field_row, _doc, past_case in rows:
            entry = grouped.setdefault(
                past_case.id, {"case_id": past_case.id, "status": past_case.status}
            )
            entry[field_row.field_name] = _coerce(field_row.value)
        return list(grouped.values())

    # ------------------------------------------------------------------
    async def _make_decision(
        self,
        case: Case,
        ctx: RuleContext,
        rule_outcome: RuleRunResult,
        hits: list[SearchHit],
        trace_id: str,
        result: OrchestrationResult,
    ) -> tuple[DecisionPayload, list[str]]:
        """决定走确定性决策还是 Decision Agent（文档 §5.4）。"""
        triggers = self._agent_triggers(case, ctx, rule_outcome, hits)
        use_agent = bool(triggers) and self.mode != AGENT_MODE_FIXED

        if not use_agent:
            return self._deterministic_decision(ctx, rule_outcome, hits), []

        state = self._build_agent_state(case, ctx, rule_outcome, hits)
        tool_ctx = make_tool_context(
            self.session,
            case,
            self.gateway,
            actor="decision_agent",
            trace_id=trace_id,
            verifier_passed=False,
        )
        # D 组：先做一次只读制度检索（Policy Researcher），再进入 Agent
        if self.mode == AGENT_MODE_AGENT_RESEARCHER:
            await self.registry.execute(
                "search_policy",
                {"query": f"{case.category} 报销 额度 审批 例外 条款", "top_k": 5},
                tool_ctx,
            )

        run = await self.agent.run(tool_ctx, state)
        result.agent_steps = run.steps
        result.tool_calls = len(run.tool_calls)
        return run.decision, triggers

    def _agent_triggers(
        self,
        case: Case,
        ctx: RuleContext,
        rule_outcome: RuleRunResult,
        hits: list[SearchHit],
    ) -> list[str]:
        """Agent 触发条件（文档 §5.4）。"""
        triggers: list[str] = []

        required = REQUIRED_DOCUMENTS.get(case.category, ())
        present = set(ctx.document_types)
        if any(d.value not in present for d in required):
            triggers.append(TRIGGER_MISSING_DOCS)

        if ctx.confidences and min(ctx.confidences.values()) < 0.7:
            triggers.append(TRIGGER_LOW_CONFIDENCE)

        failed = {r.rule_name for r in rule_outcome.failed}
        if {"amount_matches_bank", "amount_matches_receipt"} & failed:
            triggers.append(TRIGGER_AMOUNT_MISMATCH)
        if "duplicate_invoice" in failed:
            triggers.append(TRIGGER_DUPLICATE)
        if {"category_limit", "vendor_check", "approval_required"} & failed:
            triggers.append(TRIGGER_POLICY_CONFLICT)
        if not hits:
            triggers.append(TRIGGER_NO_POLICY)

        other_failed = failed - {
            "amount_matches_bank",
            "amount_matches_receipt",
            "duplicate_invoice",
            "category_limit",
            "vendor_check",
            "approval_required",
        }
        if other_failed:
            triggers.append(TRIGGER_RULE_FAILED)

        # 去重保序
        seen: set[str] = set()
        ordered = []
        for t in triggers:
            if t not in seen:
                seen.add(t)
                ordered.append(t)
        return ordered

    def _deterministic_decision(
        self,
        ctx: RuleContext,
        rule_outcome: RuleRunResult,
        hits: list[SearchHit],
    ) -> DecisionPayload:
        """固定工作流决策：不调用模型，完全由规则与证据推导（实验组 A）。"""
        evidence_ids = [e.evidence_id for e in ctx.policy_evidence][:5]
        evidence_ids += [ctx.evidence_by_field[f] for f in ctx.evidence_by_field][:3]

        if rule_outcome.failed:
            high = [r for r in rule_outcome.failed if r.severity.value == "high"]
            return DecisionPayload(
                decision="escalate",
                risk_level="high" if high else "medium",
                confidence=0.8,
                summary="规则校验未通过：" + "；".join(r.message for r in rule_outcome.failed[:3]),
                evidence_ids=evidence_ids,
                failed_checks=rule_outcome.failed_names,
                missing_items=[],
                proposed_actions=["request_human_review"],
            )

        if not hits:
            return DecisionPayload(
                decision="need_info",
                risk_level="medium",
                confidence=0.6,
                summary="未检索到可引用的制度条款，无法确认合规性，请补充或等待制度更新。",
                evidence_ids=evidence_ids,
                failed_checks=[],
                missing_items=["policy_evidence"],
                proposed_actions=["request_more_documents"],
            )

        return DecisionPayload(
            decision="approve",
            risk_level=rule_outcome.risk.level,
            confidence=min(0.97, 0.75 + 0.2 * (1 - rule_outcome.risk.score)),
            summary=(
                f"规则校验全部通过（风险分 {rule_outcome.risk.score:.2f}），"
                f"已引用 {len(ctx.policy_evidence)} 条制度条款作为依据。"
            ),
            evidence_ids=evidence_ids,
            failed_checks=[],
            missing_items=[],
            proposed_actions=["approve"],
        )

    def _build_agent_state(
        self,
        case: Case,
        ctx: RuleContext,
        rule_outcome: RuleRunResult,
        hits: list[SearchHit],
    ) -> AgentState:
        required = REQUIRED_DOCUMENTS.get(case.category, ())
        present = set(ctx.document_types)
        missing_docs = [
            DOCUMENT_TYPE_LABELS.get(d.value, d.value) for d in required if d.value not in present
        ]
        # 字段级缺失同样视为材料不完整（例如有发票但缺发票号）
        for doc_type, doc_id in ctx.document_ids.items():
            fields = ctx.by_document.get(doc_id, {})
            if doc_type == DocumentType.INVOICE.value:
                for name in ("invoice_no", "amount", "date", "vendor"):
                    if not fields.get(name):
                        label = FIELD_LABELS.get(name, name)
                        if label not in missing_docs:
                            missing_docs.append(label)

        candidate_evidence = [
            {
                "evidence_id": e.evidence_id,
                "source_type": e.source_type,
                "source_id": e.source_id,
                "section": e.quote[:40],
                "quote": e.quote,
                "score": e.score,
            }
            for e in ctx.policy_evidence
        ]
        candidate_evidence += [
            {
                "evidence_id": ev_id,
                "source_type": "document",
                "source_id": ev_id,
                "section": name,
                "quote": f"{FIELD_LABELS.get(name, name)}：{ctx.fields.get(name)}",
                "score": ctx.confidences.get(name, 0.0),
            }
            for name, ev_id in ctx.evidence_by_field.items()
        ]

        return AgentState(
            case={
                "id": case.id,
                "category": case.category,
                "amount": float(case.amount or 0),
                "description": case.description or "",
                "employee_id": case.employee_id,
            },
            extracted_fields=ctx.fields,
            failed_checks=rule_outcome.as_prompt_view(),
            candidate_evidence=candidate_evidence,
            document_types=ctx.document_types,
            missing_documents=missing_docs,
            min_extraction_confidence=min(ctx.confidences.values()) if ctx.confidences else 1.0,
            low_confidence_documents=list(ctx.__dict__.get("_low_confidence_documents", [])),
            policy_limits=ctx.policy_limits,
            risk_hint=rule_outcome.risk.level,
        )

    async def _persist_decision(
        self,
        case: Case,
        decision: DecisionPayload,
        result: OrchestrationResult,
        trace_id: str,
    ) -> None:
        self.session.add(
            Decision(
                id=new_id("dec"),
                case_id=case.id,
                decision=decision.decision,
                confidence=decision.confidence,
                risk_level=decision.risk_level,
                reason=decision.summary,
                evidence_ids=decision.evidence_ids,
            )
        )
        case.risk_level = decision.risk_level
        await emit_trace(
            self.session,
            case_id=case.id,
            trace_id=trace_id,
            event_type=EVENT_DECISION_MADE,
            actor="decision_agent" if result.agent_used else "fixed_workflow",
            payload={
                "decision": decision.as_dict(),
                "agent_used": result.agent_used,
                "agent_triggers": result.agent_triggers,
                "mode": self.mode,
            },
        )
        await self.session.flush()

    async def _verify(
        self,
        case: Case,
        decision: DecisionPayload,
        ctx: RuleContext,
        rule_outcome: RuleRunResult,
        trace_id: str,
        result: OrchestrationResult,
    ):
        if self.mode == AGENT_MODE_AGENT:
            # 实验组 B：不做 Verifier，直接放行（用于对照实验）
            from ..gateway.base import VerificationResult

            return VerificationResult.passed_result("mode=single_agent，跳过 Verifier（对照组）")

        candidate_evidence = [
            {"evidence_id": e.evidence_id, "source_type": e.source_type, "quote": e.quote}
            for e in ctx.policy_evidence
        ] + [
            {"evidence_id": ev_id, "source_type": "document", "quote": f"{FIELD_LABELS.get(n, n)}：{ctx.fields.get(n)}"}
            for n, ev_id in ctx.evidence_by_field.items()
        ]

        verification = await self.verifier.verify(
            decision=decision.as_dict(),
            case={"id": case.id, "category": case.category, "amount": case.amount},
            rule_results=[r.as_dict() for r in rule_outcome.results],
            candidate_evidence=candidate_evidence,
            policy_evidence=[
                {"section": e.source_id, "quote": e.quote} for e in ctx.policy_evidence
            ],
        )
        await emit_trace(
            self.session,
            case_id=case.id,
            trace_id=trace_id,
            event_type=EVENT_VERIFIER_FINISHED,
            actor="verifier",
            payload={
                "status": verification.status,
                "reason": verification.reason,
                "failed_checks": verification.failed_checks,
                "suggested_action": verification.suggested_action,
            },
            model_record=verification.call,
        )
        return verification

    async def _execute_erp(
        self,
        case: Case,
        decision: DecisionPayload | None,
        result: OrchestrationResult,
        trace_id: str,
        *,
        approved_by: str | None,
    ) -> None:
        gateway = ActionGateway(self.session)
        vendor = str(
            (await load_case_fields(self.session, case.id)).values.get("vendor") or ""
        )
        payload = gateway.build_payload(case, vendor=vendor, description=case.description or "")

        outcome = await gateway.submit(
            case,
            payload,
            approved_by=approved_by,
            verifier_passed=result.verifier_status in {"passed", ""},
            trace_id=trace_id,
        )
        result.erp = outcome.as_dict()

        if outcome.status in {"confirmed", "duplicate_prevented"}:
            await self._advance(case, CaseEvent.ERP_CONFIRMED, result, trace_id)
            result.status = CaseStatus.COMPLETED.value
            await emit_trace(
                self.session,
                case_id=case.id,
                trace_id=trace_id,
                event_type=EVENT_CASE_COMPLETED,
                actor="orchestrator",
                payload={"erp": outcome.as_dict(), "decision": result.decision},
            )
        elif outcome.status in {"unknown", "submitted"}:
            await self._advance(case, CaseEvent.ERP_UNKNOWN, result, trace_id)
            result.status = CaseStatus.ESCALATED.value
        else:
            # ERP 明确失败：单据未能落地，走补偿
            await self._advance(
                case, CaseEvent.COMPENSATED, result, trace_id, context={"erp": outcome.as_dict()}
            )
            result.status = (await self._reload(case.id)).status
            result.errors.append(outcome.message)

    async def _latest_decision(self, case_id: str) -> DecisionPayload | None:
        row = (
            await self.session.execute(
                select(Decision)
                .where(Decision.case_id == case_id)
                .order_by(Decision.created_at.desc())
            )
        ).scalars().first()
        if row is None:
            return None
        row_checks = (
            await self.session.execute(select(Check).where(Check.case_id == case_id))
        ).scalars().all()
        return DecisionPayload(
            decision=row.decision,  # type: ignore[arg-type]
            risk_level=row.risk_level,  # type: ignore[arg-type]
            confidence=row.confidence,
            summary=row.reason or "（历史决策）",
            evidence_ids=list(row.evidence_ids or []),
            failed_checks=[c.rule_name for c in row_checks if c.status == "failed"],
            missing_items=[],
            proposed_actions=[],
        )


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

_DEFAULT_VENDOR_WHITELIST = ("华住酒店", "中国铁路", "京东", "晨光文具", "海底捞", "滴滴出行")
_DEFAULT_VENDOR_BLACKLIST = ("鑫鑫贸易", "宏达咨询", "异常商户")


def load_vendor_lists() -> tuple[list[str], list[str]]:
    """加载供应商名单（data/vendors.json 可覆盖）。"""
    path = PROJECT_ROOT / "data" / "vendors.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return (
                list(data.get("whitelist") or _DEFAULT_VENDOR_WHITELIST),
                list(data.get("blacklist") or _DEFAULT_VENDOR_BLACKLIST),
            )
        except Exception:  # noqa: BLE001 - 配置损坏时退回默认值
            pass
    return list(_DEFAULT_VENDOR_WHITELIST), list(_DEFAULT_VENDOR_BLACKLIST)


def _coerce(raw: Any) -> Any:
    if raw is None:
        return None
    text = str(raw).strip()
    try:
        if "." in text:
            return float(text)
        if text.isdigit():
            return int(text)
    except ValueError:
        pass
    return text
