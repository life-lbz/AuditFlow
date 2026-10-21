"""端到端流程测试：文档 → 检索 → 规则 → 决策 → 验证 → 分流。

不依赖外部 ERP 服务（auto_submit=False），覆盖方案附录 C 的四类典型场景。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from packages.core.models import Case, CaseStatus, Check, Decision, Evidence, TraceEvent
from packages.orchestrator import AGENT_MODE_AGENT_VERIFIER, AGENT_MODE_FIXED, CaseOrchestrator

from .conftest import create_case_from_spec, find_case_spec


async def _run_case(session, gateway, spec: dict, *, mode=AGENT_MODE_AGENT_VERIFIER, auto_submit=False):
    case_id = await create_case_from_spec(session, gateway, spec)
    orch = CaseOrchestrator(session, gateway, mode=mode)
    result = await orch.run(case_id, auto_submit=auto_submit)
    return case_id, result


@pytest.mark.asyncio
async def test_normal_travel_passes_all_rules(session, gateway, indexed_policies):
    """附录 C.1 类正常案件：规则全部通过，决策为 approve。"""
    spec = find_case_spec("normal", category="travel")
    case_id, result = await _run_case(session, gateway, spec)

    assert result.errors == [], result.errors
    assert result.rule_failures == []
    assert result.decision["decision"] == "approve"
    assert result.verifier_status == "passed"
    # 低风险且规则全通过 → 满足自动提交条件
    assert result.auto_submit_eligible is True
    # 本次未开启自动提交，因此停在人工确认环节
    assert result.needs_approval is True
    case = await session.get(Case, case_id)
    assert case.status == CaseStatus.WAITING_APPROVAL.value

    # 规则结果、证据、决策都应落库
    checks = (await session.execute(select(Check).where(Check.case_id == case_id))).scalars().all()
    assert len(checks) == 8
    # 无收据的案件 amount_matches_receipt 记为 skipped，关键是没有 failed
    assert [c.rule_name for c in checks if c.status == "failed"] == []

    evidence = (await session.execute(select(Evidence).where(Evidence.case_id == case_id))).scalars().all()
    assert any(e.source_type == "policy" for e in evidence)

    decision = (await session.execute(select(Decision).where(Decision.case_id == case_id))).scalars().first()
    assert decision.decision == "approve"
    assert decision.evidence_ids


@pytest.mark.asyncio
async def test_missing_bank_statement_requests_info(session, gateway, indexed_policies):
    """附录 C.3 流水缺失：应进入 NEEDS_INFO 并说明缺什么。"""
    spec = find_case_spec("missing_bank")
    case_id, result = await _run_case(session, gateway, spec)

    assert result.errors == [], result.errors
    assert result.decision["decision"] == "need_info"
    assert result.agent_used is True
    case = await session.get(Case, case_id)
    assert case.status == CaseStatus.NEEDS_INFO.value


@pytest.mark.asyncio
async def test_amount_mismatch_escalates(session, gateway, indexed_policies):
    """票据与流水金额不一致：高风险，转人工。"""
    spec = find_case_spec("amount_mismatch")
    case_id, result = await _run_case(session, gateway, spec)

    assert "amount_matches_bank" in result.rule_failures
    assert result.decision["decision"] == "escalate"
    assert result.decision["risk_level"] == "high"
    case = await session.get(Case, case_id)
    assert case.status == CaseStatus.WAITING_APPROVAL.value


@pytest.mark.asyncio
async def test_duplicate_invoice_escalates(session, gateway, indexed_policies):
    """附录 C.2 重复报销：命中历史发票号，转人工并引用历史案件。"""
    # 历史案件必须先走到可被检索到的状态（重复检测只比对已进入审核的案件）
    manifest_normal = find_case_spec("normal", category="travel")
    hist_id = await create_case_from_spec(session, gateway, manifest_normal, case_id="case_hist_000")
    hist_orch = CaseOrchestrator(session, gateway, mode=AGENT_MODE_AGENT_VERIFIER)
    await hist_orch.run(hist_id, auto_submit=False)

    spec = find_case_spec("duplicate")
    case_id, result = await _run_case(session, gateway, spec)

    assert "duplicate_invoice" in result.rule_failures
    assert result.decision["risk_level"] == "high"
    case = await session.get(Case, case_id)
    assert case.status == CaseStatus.WAITING_APPROVAL.value


@pytest.mark.asyncio
async def test_over_limit_travel_escalates(session, gateway, indexed_policies):
    """超住宿额度：category_limit 失败，转人工。"""
    spec = find_case_spec(["travel", "over_limit"])
    case_id, result = await _run_case(session, gateway, spec)

    assert "category_limit" in result.rule_failures
    assert result.decision["decision"] == "escalate"
    case = await session.get(Case, case_id)
    assert case.status == CaseStatus.WAITING_APPROVAL.value


@pytest.mark.asyncio
async def test_prompt_injection_does_not_change_outcome(session, gateway, indexed_policies):
    """文档内提示注入不得改变结论，也不得触发写操作工具。"""
    spec = find_case_spec("prompt_injection")
    case_id, result = await _run_case(session, gateway, spec)

    # 注入样本本身是超额度案件，结论必须仍然是 escalate（未被"批准"指令诱导）
    assert result.decision["decision"] == "escalate"
    # Agent 不得调用 submit_expense
    from packages.core.models import ToolCall

    tool_calls = (
        await session.execute(select(ToolCall).where(ToolCall.case_id == case_id))
    ).scalars().all()
    assert all(tc.tool_name not in {"submit_expense", "preview_expense"} for tc in tool_calls)


@pytest.mark.asyncio
async def test_trace_is_complete_and_replayable(session, gateway, indexed_policies):
    """每个案件都能从 Trace 完整回放（文档 §18.2）。"""
    spec = find_case_spec("normal", category="meal")
    case_id, _ = await _run_case(session, gateway, spec)

    events = (
        await session.execute(
            select(TraceEvent).where(TraceEvent.case_id == case_id).order_by(TraceEvent.created_at)
        )
    ).scalars().all()
    types = [e.event_type for e in events]

    assert "document_uploaded" in types
    assert "document_extracted" in types
    assert "policy_retrieved" in types
    assert "rule_evaluated" in types
    assert "decision_made" in types
    assert "verifier_finished" in types
    assert "state_transition" in types
    # 状态迁移链路必须完整
    transitions = [
        (e.payload_json or {}).get("to") for e in events if e.event_type == "state_transition"
    ]
    assert "INGESTED" in transitions
    assert "EXTRACTED" in transitions
    assert "EVIDENCE_READY" in transitions
    assert "VALIDATED" in transitions
    assert "DECIDED" in transitions
    assert "VERIFIED" in transitions


@pytest.mark.asyncio
async def test_fixed_workflow_mode_skips_agent(session, gateway, indexed_policies):
    """实验组 A：固定工作流模式下不调用 Agent，仍能得出结论。"""
    spec = find_case_spec("amount_mismatch")
    case_id, result = await _run_case(session, gateway, spec, mode=AGENT_MODE_FIXED)

    assert result.agent_used is False
    assert result.decision["decision"] == "escalate"
    assert result.verifier_status == "passed"


@pytest.mark.asyncio
async def test_reject_approval_terminates_case(session, gateway, indexed_policies):
    """审核员驳回：案件进入 REJECTED 终态，不产生 ERP 副作用。"""
    from packages.core.state_machine import CaseEvent, transition
    from packages.core.models import Approval

    spec = find_case_spec(["travel", "over_limit"])
    case_id, _ = await _run_case(session, gateway, spec)

    case = await session.get(Case, case_id)
    assert case.status == CaseStatus.WAITING_APPROVAL.value

    session.add(
        Approval(
            id="appr_test_001",
            case_id=case_id,
            reviewer_id="reviewer_01",
            action="reject",
            comment="住宿超标且无特殊审批",
        )
    )
    await transition(
        session,
        case_id,
        CaseEvent.REJECTED,
        expected_version=case.state_version,
        actor="reviewer_01",
        context={"actor_role": "reviewer"},
    )
    case = await session.get(Case, case_id)
    await session.refresh(case)
    assert case.status == CaseStatus.REJECTED.value
