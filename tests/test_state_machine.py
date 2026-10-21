"""状态机测试（文档 §5.6）：主链路、非法迁移、守卫、乐观锁、fatal。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.core.models import Base, Case, CaseStatus, TraceEvent
from packages.core.state_machine import (
    CaseEvent,
    GuardFailedError,
    OptimisticLockError,
    TransitionError,
    transition,
)

VALID_DECISION = {
    "decision": {"decision": "approve", "risk_level": "low", "confidence": 0.9, "summary": "ok"}
}

# CREATED -> VERIFIED 主链路（事件, context）
CHAIN_TO_VERIFIED: list[tuple[CaseEvent, dict]] = [
    (CaseEvent.DOCUMENTS_UPLOADED, {"documents": ["doc_1"]}),
    (CaseEvent.EXTRACT_STARTED, {}),
    (CaseEvent.POLICY_SEARCH_COMPLETED, {}),
    (CaseEvent.RULES_COMPLETED, {}),
    (CaseEvent.DECISION_GENERATED, VALID_DECISION),
    (CaseEvent.VERIFICATION_PASSED, {}),
]


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield maker
    await engine.dispose()


async def make_case(factory, **kwargs) -> Case:
    async with factory() as s:
        case = Case(
            id=f"case_{uuid.uuid4().hex[:12]}",
            employee_id="emp_001",
            category="travel",
            amount=100.0,
            trace_id=f"trace_{uuid.uuid4().hex[:12]}",
            **kwargs,
        )
        s.add(case)
        await s.commit()
        return case


async def advance(s: AsyncSession, case: Case, chain: list[tuple[CaseEvent, dict]]) -> Case:
    for event, ctx in chain:
        case = await transition(s, case.id, event, expected_version=case.state_version,
                                actor="test", context=ctx)
    return case


async def test_happy_path_to_completed(factory):
    case = await make_case(factory)
    async with factory() as s:
        case = await advance(s, case, CHAIN_TO_VERIFIED)
        assert case.status == CaseStatus.VERIFIED.value

        case = await advance(s, case, [
            (CaseEvent.APPROVAL_REQUIRED, {}),
            (CaseEvent.APPROVED, {"actor_role": "reviewer"}),
            (CaseEvent.ERP_CONFIRMED, {}),
        ])
        assert case.status == CaseStatus.COMPLETED.value
        assert case.state_version == len(CHAIN_TO_VERIFIED) + 3

        # 每次迁移都应写入 Trace 事件（文档 §18）
        result = await s.execute(
            select(TraceEvent).where(TraceEvent.case_id == case.id)
        )
        events = result.scalars().all()
        assert len(events) == len(CHAIN_TO_VERIFIED) + 3
        assert all(e.event_type == "state_transition" for e in events)
        assert events[-1].payload_json["to"] == CaseStatus.COMPLETED.value


async def test_illegal_transition_rejected(factory):
    case = await make_case(factory)
    async with factory() as s:
        with pytest.raises(TransitionError):
            await transition(s, case.id, CaseEvent.DECISION_GENERATED,
                             expected_version=case.state_version, actor="test")


async def test_guard_rejects_missing_documents(factory):
    case = await make_case(factory)
    async with factory() as s:
        with pytest.raises(GuardFailedError):
            await transition(s, case.id, CaseEvent.DOCUMENTS_UPLOADED,
                             expected_version=case.state_version, actor="test")


async def test_decision_schema_guard(factory):
    case = await make_case(factory)
    async with factory() as s:
        case = await advance(s, case, CHAIN_TO_VERIFIED[:4])  # 推进到 VALIDATED
        with pytest.raises(GuardFailedError):
            await transition(s, case.id, CaseEvent.DECISION_GENERATED,
                             expected_version=case.state_version, actor="test",
                             context={"decision": {"decision": "approve"}})  # 字段不全


async def test_optimistic_lock_conflict(factory):
    case = await make_case(factory)
    async with factory() as s:
        with pytest.raises(OptimisticLockError):
            await transition(s, case.id, CaseEvent.DOCUMENTS_UPLOADED,
                             expected_version=case.state_version + 1, actor="test",
                             context={"documents": ["doc_1"]})


async def test_fatal_error_from_active_state(factory):
    case = await make_case(factory)
    async with factory() as s:
        case = await transition(s, case.id, CaseEvent.FATAL_ERROR,
                                expected_version=case.state_version, actor="test")
        assert case.status == CaseStatus.FAILED.value
        # FAILED 是终态：不再接受任何事件
        with pytest.raises(TransitionError):
            await transition(s, case.id, CaseEvent.DOCUMENTS_UPLOADED,
                             expected_version=case.state_version, actor="test",
                             context={"documents": ["doc_1"]})


async def test_auto_submit_guard(factory):
    """AUTO_SUBMIT_ALLOWED 仅对低风险案件放行（文档 §2.5）。"""
    # 非低风险：拒绝
    case = await make_case(factory)
    async with factory() as s:
        case = await advance(s, case, CHAIN_TO_VERIFIED)
        with pytest.raises(GuardFailedError):
            await transition(s, case.id, CaseEvent.AUTO_SUBMIT_ALLOWED,
                             expected_version=case.state_version, actor="test",
                             context={"auto_submit_allowed": True})

    # 低风险：直接 VERIFIED -> EXECUTING
    case2 = await make_case(factory, risk_level="low")
    async with factory() as s:
        case2 = await advance(s, case2, CHAIN_TO_VERIFIED)
        case2 = await transition(s, case2.id, CaseEvent.AUTO_SUBMIT_ALLOWED,
                                 expected_version=case2.state_version, actor="test",
                                 context={"auto_submit_allowed": True})
        assert case2.status == CaseStatus.EXECUTING.value


async def test_approved_guard_requires_reviewer(factory):
    case = await make_case(factory)
    async with factory() as s:
        case = await advance(s, case, CHAIN_TO_VERIFIED)
        case = await transition(s, case.id, CaseEvent.APPROVAL_REQUIRED,
                                expected_version=case.state_version, actor="test")
        with pytest.raises(GuardFailedError):
            await transition(s, case.id, CaseEvent.APPROVED,
                             expected_version=case.state_version, actor="emp_001",
                             context={"actor_role": "employee"})
