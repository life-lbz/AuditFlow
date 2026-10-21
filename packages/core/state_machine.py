"""案件状态机（文档 §5：14 个状态 + §5.6 正式迁移表 + 乐观锁）。

所有状态迁移必须通过 transition() 执行，不允许直接改 case.status。
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Case, CaseStatus, TraceEvent


class CaseEvent(str, enum.Enum):
    DOCUMENTS_UPLOADED = "documents_uploaded"
    EXTRACT_STARTED = "extract_started"
    POLICY_SEARCH_COMPLETED = "policy_search_completed"
    RULES_COMPLETED = "rules_completed"
    DECISION_GENERATED = "decision_generated"
    VERIFICATION_PASSED = "verification_passed"
    APPROVAL_REQUIRED = "approval_required"
    AUTO_SUBMIT_ALLOWED = "auto_submit_allowed"
    APPROVED = "approved"
    REJECTED = "rejected"
    ERP_CONFIRMED = "erp_confirmed"
    ERP_UNKNOWN = "erp_unknown"
    COMPENSATED = "compensated"
    EVIDENCE_MISSING = "evidence_missing"
    HIGH_RISK_CONFLICT = "high_risk_conflict"
    FATAL_ERROR = "fatal_error"


class TransitionError(Exception):
    """非法迁移（当前状态不接受该事件）。"""


class GuardFailedError(Exception):
    """守卫条件不满足。"""


class OptimisticLockError(Exception):
    """乐观锁冲突：state_version 与预期不一致。"""


# 守卫函数签名：async (session, case, context) -> bool
Guard = Callable[[AsyncSession, Case, dict[str, Any]], Awaitable[bool]]


async def _always(session: AsyncSession, case: Case, context: dict[str, Any]) -> bool:
    return True


async def _has_documents(session: AsyncSession, case: Case, context: dict[str, Any]) -> bool:
    return bool(context.get("documents")) or len(case.documents) >= 1


async def _decision_schema_valid(session: AsyncSession, case: Case, context: dict[str, Any]) -> bool:
    decision = context.get("decision") or {}
    required = {"decision", "risk_level", "confidence", "summary"}
    return required.issubset(decision.keys())


async def _is_low_risk_auto_allowed(session: AsyncSession, case: Case, context: dict[str, Any]) -> bool:
    """自动提交前置（文档 §2.5）：低风险 + 人工确认信号由 context 给出。"""
    return case.risk_level == "low" and bool(context.get("auto_submit_allowed"))


async def _reviewer_authorized(session: AsyncSession, case: Case, context: dict[str, Any]) -> bool:
    return context.get("actor_role") in {"reviewer", "admin"}


@dataclass(frozen=True)
class Transition:
    event: CaseEvent
    from_status: CaseStatus
    to_status: CaseStatus
    guard: Guard = _always
    side_effect: str = ""
    retry_policy: str = ""


# 文档 §5.6 正式状态迁移表
TRANSITIONS: tuple[Transition, ...] = (
    Transition(CaseEvent.DOCUMENTS_UPLOADED, CaseStatus.CREATED, CaseStatus.INGESTED,
               guard=_has_documents, side_effect="保存哈希和存储地址", retry_policy="上传失败重试 2 次"),
    Transition(CaseEvent.EXTRACT_STARTED, CaseStatus.INGESTED, CaseStatus.EXTRACTED,
               side_effect="OCR、字段和 bbox", retry_policy="单文档失败重试 1 次"),
    Transition(CaseEvent.POLICY_SEARCH_COMPLETED, CaseStatus.EXTRACTED, CaseStatus.EVIDENCE_READY,
               side_effect="保存候选证据", retry_policy="检索超时可重试 1 次"),
    Transition(CaseEvent.RULES_COMPLETED, CaseStatus.EVIDENCE_READY, CaseStatus.VALIDATED,
               side_effect="保存规则结果", retry_policy="规则执行不重试"),
    Transition(CaseEvent.DECISION_GENERATED, CaseStatus.VALIDATED, CaseStatus.DECIDED,
               guard=_decision_schema_valid, side_effect="保存 Decision", retry_policy="解析失败修复 1 次"),
    Transition(CaseEvent.VERIFICATION_PASSED, CaseStatus.DECIDED, CaseStatus.VERIFIED,
               side_effect="保存验证结果", retry_policy="失败可回退一次"),
    Transition(CaseEvent.APPROVAL_REQUIRED, CaseStatus.VERIFIED, CaseStatus.WAITING_APPROVAL,
               side_effect="创建人工任务", retry_policy="通知失败重试"),
    Transition(CaseEvent.AUTO_SUBMIT_ALLOWED, CaseStatus.VERIFIED, CaseStatus.EXECUTING,
               guard=_is_low_risk_auto_allowed, side_effect="创建幂等执行记录", retry_policy="见 ERP 故障矩阵"),
    Transition(CaseEvent.APPROVED, CaseStatus.WAITING_APPROVAL, CaseStatus.EXECUTING,
               guard=_reviewer_authorized, side_effect="保存审批记录", retry_policy="不自动重试"),
    Transition(CaseEvent.ERP_CONFIRMED, CaseStatus.EXECUTING, CaseStatus.COMPLETED,
               side_effect="保存 ERP 单号和快照", retry_policy="不允许重复提交"),
    Transition(CaseEvent.ERP_UNKNOWN, CaseStatus.EXECUTING, CaseStatus.ESCALATED,
               side_effect="创建人工核查任务", retry_policy="只查不写"),
    Transition(CaseEvent.EVIDENCE_MISSING, CaseStatus.EXTRACTED, CaseStatus.NEEDS_INFO,
               side_effect="创建补材料任务", retry_policy="等待用户"),
    Transition(CaseEvent.HIGH_RISK_CONFLICT, CaseStatus.VALIDATED, CaseStatus.ESCALATED,
               side_effect="提升风险等级", retry_policy="等待人工"),
    # ---- 以下迁移补全方案 §5.3 异常回路，使补材料/驳回/补偿路径可闭环 ----
    Transition(CaseEvent.DOCUMENTS_UPLOADED, CaseStatus.NEEDS_INFO, CaseStatus.EXTRACTED,
               guard=_has_documents, side_effect="保存补充材料哈希", retry_policy="上传失败重试 2 次"),
    Transition(CaseEvent.EVIDENCE_MISSING, CaseStatus.WAITING_APPROVAL, CaseStatus.NEEDS_INFO,
               side_effect="审核员要求补材料", retry_policy="等待用户"),
    Transition(CaseEvent.EVIDENCE_MISSING, CaseStatus.VERIFIED, CaseStatus.NEEDS_INFO,
               side_effect="Agent 结论为 need_info，请求补材料", retry_policy="等待用户"),
    Transition(CaseEvent.REJECTED, CaseStatus.WAITING_APPROVAL, CaseStatus.REJECTED,
               guard=_reviewer_authorized, side_effect="保存驳回意见", retry_policy="不自动重试"),
    Transition(CaseEvent.REJECTED, CaseStatus.VERIFIED, CaseStatus.REJECTED,
               side_effect="高置信度驳回结论直接终结", retry_policy="不自动重试"),
    Transition(CaseEvent.APPROVED, CaseStatus.ESCALATED, CaseStatus.EXECUTING,
               guard=_reviewer_authorized, side_effect="人工核查通过后执行", retry_policy="不自动重试"),
    Transition(CaseEvent.REJECTED, CaseStatus.ESCALATED, CaseStatus.REJECTED,
               guard=_reviewer_authorized, side_effect="人工核查后驳回落库", retry_policy="不自动重试"),
    Transition(CaseEvent.COMPENSATED, CaseStatus.EXECUTING, CaseStatus.ROLLED_BACK,
               side_effect="记录 ERP 补偿结果", retry_policy="补偿失败转人工"),
)

# 任意活动状态 -> FAILED（fatal_error）
TERMINAL_STATES = (
    CaseStatus.COMPLETED,
    CaseStatus.FAILED,
    CaseStatus.ROLLED_BACK,
    CaseStatus.REJECTED,
)
ACTIVE_STATES = tuple(s for s in CaseStatus if s not in TERMINAL_STATES)
FATAL_TRANSITIONS: tuple[Transition, ...] = tuple(
    Transition(CaseEvent.FATAL_ERROR, s, CaseStatus.FAILED,
               side_effect="保存错误快照", retry_policy="不允许无限重试")
    for s in ACTIVE_STATES
)

ALL_TRANSITIONS: tuple[Transition, ...] = TRANSITIONS + FATAL_TRANSITIONS

_INDEX: dict[tuple[CaseStatus, CaseEvent], Transition] = {
    (t.from_status, t.event): t for t in ALL_TRANSITIONS
}


def allowed_events(status: CaseStatus) -> list[CaseEvent]:
    return [e for (s, e) in _INDEX if s == status]


async def transition(
    session: AsyncSession,
    case_id: str,
    event: CaseEvent,
    expected_version: int,
    actor: str,
    context: dict[str, Any] | None = None,
) -> Case:
    """统一状态迁移入口（文档 §5.6）。

    - 校验迁移合法性（状态 + 事件）
    - 执行守卫条件
    - 乐观锁：UPDATE ... WHERE state_version = expected_version
    - 写入 Trace 事件
    """
    context = context or {}
    case = await session.get(Case, case_id)
    if case is None:
        raise TransitionError(f"case not found: {case_id}")

    current = CaseStatus(case.status)
    t = _INDEX.get((current, event))
    if t is None:
        raise TransitionError(f"illegal transition: {current.value} + {event.value}")

    if not await t.guard(session, case, context):
        raise GuardFailedError(f"guard failed: {current.value} + {event.value} (guard={t.guard.__name__})")

    # 乐观锁并发控制
    result = await session.execute(
        update(Case)
        .where(Case.id == case_id, Case.state_version == expected_version)
        .values(status=t.to_status.value, state_version=expected_version + 1)
    )
    if result.rowcount != 1:
        raise OptimisticLockError(
            f"case {case_id}: expected version {expected_version}, concurrent modification detected"
        )

    await session.refresh(case)

    # Trace（文档 §18：所有状态迁移可追踪）
    trace = TraceEvent(
        id=f"evt_{uuid.uuid4().hex[:12]}",
        trace_id=case.trace_id or f"trace_{case_id}",
        case_id=case_id,
        event_type="state_transition",
        actor=actor,
        payload_json={
            "event": event.value,
            "from": current.value,
            "to": t.to_status.value,
            "side_effect": t.side_effect,
            "state_version": case.state_version,
        },
    )
    session.add(trace)
    await session.commit()
    return case
