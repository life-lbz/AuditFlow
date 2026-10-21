"""Decision Agent 运行时（文档 §10.3 循环 / §10.4 预算 / ADR-001）。

设计要点：
- 单 Agent，仅在异常路径被调用；正常流程由确定性编排驱动。
- 每轮只允许选择一个动作：调用一个只读工具，或给出最终结论。
- submit_expense 不进入 Agent 的可选工具集：Agent 只能提出 proposed_actions，
  真正的 ERP 写入由编排层在 Verifier 通过且获得批准后经由 Action Gateway 执行（ADR-003）。
- 预算耗尽、校验失败、工具越权都不会静默通过，一律降级为 escalate。
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import ValidationError

from ..core.config import get_settings
from ..core.models import Case
from ..core.tracing import EVENT_AGENT_STEP, emit_trace
from ..gateway.base import ModelGateway
from ..tools.registry import ToolContext, ToolRegistry
from .prompts import PROMPT_VERSION
from .schemas import AgentRunResult, AgentState, DecisionPayload, PolicyViolation

# Agent 可用的工具白名单：显式排除高风险写操作
READ_ONLY_TOOLS = (
    "extract_document",
    "search_policy",
    "match_bank_transaction",
    "check_duplicate",
    "get_erp_record",
)
HITL_TOOL = "request_human"
EXCLUDED_TOOLS = ("submit_expense", "preview_expense")


class DecisionAgentRuntime:
    """带预算与检查点的单 Agent 工具循环。"""

    def __init__(self, gateway: ModelGateway, registry: ToolRegistry | None = None) -> None:
        self.gateway = gateway
        self.registry = registry or ToolRegistry()
        self.settings = get_settings()

    def allowed_tools(self) -> list[str]:
        available = set(self.registry.names())
        allowed = [t for t in (*READ_ONLY_TOOLS, HITL_TOOL) if t in available]
        return [t for t in allowed if t not in EXCLUDED_TOOLS]

    async def run(self, ctx: ToolContext, state: AgentState) -> AgentRunResult:
        budget = {
            "tool_calls": self.settings.agent_max_tool_calls,
            "replans": self.settings.agent_max_replans,
            "retries_per_tool": self.settings.agent_retries_per_tool,
        }
        state.remaining_budget = dict(budget)
        state.allowed_tools = self.allowed_tools()
        tool_specs = self.registry.specs(state.allowed_tools)

        tool_calls: list[dict[str, Any]] = []
        validation_retries = 0

        for step in range(1, self.settings.agent_max_steps + 1):
            # 预算耗尽 → 转人工（文档 §10.4）
            if budget["tool_calls"] <= 0:
                return AgentRunResult(
                    decision=self._escalate("agent_budget_exhausted", state),
                    steps=step - 1,
                    tool_calls=tool_calls,
                    exhausted=True,
                )

            action = await self.gateway.choose_action(state.as_model_view(), tool_specs)
            await emit_trace(
                ctx.session,
                case_id=ctx.case_id,
                trace_id=ctx.trace_id,
                event_type=EVENT_AGENT_STEP,
                actor="decision_agent",
                payload={
                    "step": step,
                    "action_type": action.type,
                    "tool_name": action.tool_name,
                    "arguments": action.arguments,
                    "rationale": action.rationale,
                    "prompt_version": PROMPT_VERSION,
                },
                model_record=action.call,
            )

            # 最终结论：先校验 Schema，再返回（文档 §10.3 validate_decision）
            if action.type == "final":
                try:
                    decision = DecisionPayload.model_validate(action.payload or {})
                    decision = self._sanitize_decision(decision, state)
                    return AgentRunResult(
                        decision=decision,
                        steps=step,
                        tool_calls=tool_calls,
                        replans=budget["replans"] - self.settings.agent_max_replans,
                    )
                except ValidationError as exc:
                    validation_retries += 1
                    if validation_retries > 1:
                        return AgentRunResult(
                            decision=self._escalate("decision_schema_invalid", state),
                            steps=step,
                            tool_calls=tool_calls,
                            error=str(exc)[:400],
                        )
                    continue

            # 工具调用：越权直接判定违规（文档 §10.3）
            if not action.tool_name or action.tool_name not in state.allowed_tools:
                raise PolicyViolation(
                    f"agent attempted to call '{action.tool_name}' which is not allowed"
                )

            budget["tool_calls"] -= 1
            result = await self.registry.execute(action.tool_name, action.arguments, ctx)
            tool_calls.append(
                {
                    "step": step,
                    "tool_name": action.tool_name,
                    "arguments": action.arguments,
                    "ok": result.ok,
                    "error": result.error,
                    "duration_ms": result.duration_ms,
                }
            )
            state.previous_tool_calls.append(
                {
                    "tool_name": action.tool_name,
                    "arguments": action.arguments,
                    "status": "ok" if result.ok else "failed",
                    "error": result.error,
                }
            )
            self._absorb_tool_result(state, action.tool_name, result.data, result.ok)

            # 检查点：预算实时反映到状态，供模型下一轮感知
            state.remaining_budget = dict(budget)

        return AgentRunResult(
            decision=self._escalate("agent_step_limit", state),
            steps=self.settings.agent_max_steps,
            tool_calls=tool_calls,
            exhausted=True,
        )

    # ------------------------------------------------------------------
    def _absorb_tool_result(
        self,
        state: AgentState,
        tool_name: str,
        data: dict[str, Any],
        ok: bool,
    ) -> None:
        """把工具结果并入状态，供下一轮决策使用。"""
        if not ok:
            return
        if tool_name == "search_policy":
            for hit in data.get("hits", []):
                state.candidate_evidence.append(
                    {
                        "evidence_id": hit.get("chunk_id"),
                        "source_type": "policy",
                        "source_id": hit.get("chunk_id"),
                        "section": f"{hit.get('policy_id')} · {hit.get('section')}",
                        "quote": hit.get("content", "")[:200],
                        "score": hit.get("score", 0.0),
                    }
                )
        elif tool_name == "check_duplicate" and data.get("duplicate"):
            state.failed_checks.append(
                {
                    "rule_name": "duplicate_invoice",
                    "severity": "high",
                    "message": data.get("message", "疑似重复报销"),
                }
            )
        elif tool_name == "match_bank_transaction":
            if not data.get("matched") and data.get("reason") != "missing_bank_statement":
                state.failed_checks.append(
                    {
                        "rule_name": "amount_matches_bank",
                        "severity": "high",
                        "message": data.get("message", "流水未匹配到对应交易"),
                    }
                )
        elif tool_name == "extract_document":
            conf = data.get("min_confidence")
            if isinstance(conf, (int, float)):
                state.min_extraction_confidence = max(0.0, min(1.0, float(conf)))

    @staticmethod
    def _sanitize_decision(decision: DecisionPayload, state: AgentState) -> DecisionPayload:
        """确保引用的证据真实存在（防止模型编造 evidence_id，文档 §8.4）。"""
        valid_ids = {str(e.get("evidence_id")) for e in state.candidate_evidence if e.get("evidence_id")}
        kept = [e for e in decision.evidence_ids if e in valid_ids]
        if len(kept) != len(decision.evidence_ids):
            decision = decision.model_copy(update={"evidence_ids": kept})
        return decision

    def _escalate(self, reason: str, state: AgentState) -> DecisionPayload:
        """兜底结论：转人工，绝不静默通过（文档 §10.6 原则 4）。"""
        reasons = {
            "agent_budget_exhausted": "Agent 工具预算耗尽，未能形成确定结论",
            "decision_schema_invalid": "Agent 输出不符合 Decision Schema，无法采信",
            "agent_step_limit": "Agent 达到最大步数限制",
        }
        return DecisionPayload(
            decision="escalate",
            risk_level="high",
            confidence=0.0,
            summary=f"{reasons.get(reason, 'Agent 未能给出结论')}，转人工审核。",
            evidence_ids=[str(e.get("evidence_id")) for e in state.candidate_evidence[:3] if e.get("evidence_id")],
            failed_checks=[c.get("rule_name", "") for c in state.failed_checks if c.get("rule_name")],
            missing_items=[],
            proposed_actions=["request_human_review"],
        )


def make_tool_context(
    session,
    case: Case,
    gateway: ModelGateway,
    *,
    actor: str = "decision_agent",
    trace_id: str | None = None,
    approved_by: str | None = None,
    verifier_passed: bool = False,
) -> ToolContext:
    return ToolContext(
        session=session,
        case=case,
        gateway=gateway,
        actor=actor,
        trace_id=trace_id,
        approved_by=approved_by,
        verifier_passed=verifier_passed,
    )


def now_ms() -> int:
    return int(time.perf_counter() * 1000)
