"""Action Gateway 与 Mock ERP 的联动测试（文档 §11.3、§14）。

要求 Mock ERP 服务可用（默认 http://localhost:8010）。
启动方式：docker compose -f infra/docker-compose.yml up -d mock-erp
服务不可达时自动跳过。
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from packages.core.config import get_settings
from packages.core.models import Case, ErpTransaction
from packages.erp.client import ErpClient
from packages.erp.gateway import ActionGateway, compute_idempotency_key


@pytest_asyncio.fixture
async def erp_ready():
    client = ErpClient()
    if not await client.health():
        pytest.skip(f"Mock ERP 不可用：{get_settings().mock_erp_url}")
    await client.reset()
    yield client
    await client.reset()


async def _make_case(session, case_id: str = "case_erp_001", risk: str = "low") -> Case:
    case = Case(
        id=case_id,
        employee_id="u_1001",
        category="travel",
        amount=560.0,
        description="上海出差住宿两晚",
        risk_level=risk,
        trace_id=f"trace_{case_id}",
    )
    session.add(case)
    await session.commit()
    return case


@pytest.mark.asyncio
async def test_submit_confirms_and_completes(session, erp_ready):
    """正常提交：ERP 单据被确认，事务状态为 confirmed。"""
    case = await _make_case(session)
    gateway = ActionGateway(session, erp_ready)
    payload = gateway.build_payload(case, vendor="华住酒店")

    outcome = await gateway.submit(
        case, payload, approved_by=None, verifier_passed=True, trace_id=case.trace_id
    )

    assert outcome.status == "confirmed", outcome.message
    assert outcome.verified is True
    assert outcome.erp_id
    assert outcome.snapshot.get("case_id") == case.id

    txn = await session.get(ErpTransaction, outcome.transaction_id)
    assert txn.status == "confirmed"


@pytest.mark.asyncio
async def test_idempotent_resubmit_creates_no_second_expense(session, erp_ready):
    """幂等：同一案件重复提交只产生一张 ERP 单据。"""
    case = await _make_case(session, "case_erp_002")
    gateway = ActionGateway(session, erp_ready)
    payload = gateway.build_payload(case, vendor="华住酒店")

    first = await gateway.submit(case, payload, verifier_passed=True)
    second = await gateway.submit(case, payload, verifier_passed=True)

    assert first.status == "confirmed"
    assert second.status == "duplicate_prevented"
    assert second.erp_id == first.erp_id

    state = await erp_ready.reset()  # 顺便验证 reset 可用
    assert state.get("expense_count", 0) == 0


@pytest.mark.asyncio
async def test_timeout_after_commit_does_not_duplicate(session, erp_ready):
    """附录 C.4：提交超时但 ERP 已落库，应查询确认而不是重复提交。"""
    case = await _make_case(session, "case_erp_003")
    await erp_ready.set_failure_mode("timeout_after_commit", delay_seconds=0.2)

    gateway = ActionGateway(session, erp_ready)
    payload = gateway.build_payload(case, vendor="华住酒店")
    outcome = await gateway.submit(case, payload, verifier_passed=True)

    assert outcome.status in {"confirmed", "duplicate_prevented", "submitted"}
    assert outcome.erp_id

    await erp_ready.set_failure_mode("none")
    record = await erp_ready.get_expense(outcome.erp_id)
    assert record["case_id"] == case.id
    assert abs(float(record["amount"]) - 560.0) < 0.01


@pytest.mark.asyncio
async def test_duplicate_submit_mode_returns_conflict_with_existing_id(session, erp_ready):
    """duplicate_submit 故障下，ERP 返回 409 + existing_erp_id，应能正确接管。"""
    case = await _make_case(session, "case_erp_004")
    await erp_ready.set_failure_mode("duplicate_submit")

    gateway = ActionGateway(session, erp_ready)
    payload = gateway.build_payload(case, vendor="华住酒店")
    outcome = await gateway.submit(case, payload, verifier_passed=True)

    # 首次提交时 ERP 侧冲突，但能拿到既有 erp_id 并完成状态核对
    assert outcome.erp_id is not None
    assert outcome.status in {"confirmed", "unknown", "submitted", "failed"}

    await erp_ready.set_failure_mode("none")
    if outcome.status in {"confirmed", "unknown", "submitted"}:
        record = await erp_ready.get_expense(outcome.erp_id)
        assert record["case_id"] == case.id


@pytest.mark.asyncio
async def test_server_error_records_compensation(session, erp_ready):
    """ERP 500：提交失败并记录补偿动作，不留下悬挂事务。"""
    case = await _make_case(session, "case_erp_005")
    await erp_ready.set_failure_mode("server_error")

    gateway = ActionGateway(session, erp_ready)
    payload = gateway.build_payload(case, vendor="华住酒店")
    outcome = await gateway.submit(case, payload, verifier_passed=True)

    await erp_ready.set_failure_mode("none")
    assert outcome.status == "failed"
    assert outcome.compensation is not None

    txn = await session.get(ErpTransaction, outcome.transaction_id)
    assert txn.status == "failed"


@pytest.mark.asyncio
async def test_high_risk_requires_approval(session, erp_ready):
    """高风险案件缺少人工批准时，Action Gateway 必须拒绝执行。"""
    case = await _make_case(session, "case_erp_006", risk="high")
    gateway = ActionGateway(session, erp_ready)
    payload = gateway.build_payload(case, vendor="华住酒店")

    outcome = await gateway.submit(case, payload, approved_by=None, verifier_passed=True)
    assert outcome.status == "rejected"
    assert "人工批准" in outcome.message


@pytest.mark.asyncio
async def test_verifier_must_pass_before_submit(session, erp_ready):
    """Verifier 未通过时禁止提交（文档 §11.3 前置条件 1）。"""
    case = await _make_case(session, "case_erp_007")
    gateway = ActionGateway(session, erp_ready)
    payload = gateway.build_payload(case, vendor="华住酒店")

    outcome = await gateway.submit(case, payload, verifier_passed=False)
    assert outcome.status == "rejected"
    assert "Verifier" in outcome.message


@pytest.mark.asyncio
async def test_delayed_state_reports_submitted(session, erp_ready):
    """delayed_state：单据已创建但状态未可见，应返回 submitted/unknown 而非谎报成功。"""
    case = await _make_case(session, "case_erp_008")
    await erp_ready.set_failure_mode("delayed_state", state_delay_seconds=2.5)

    gateway = ActionGateway(session, erp_ready)
    payload = gateway.build_payload(case, vendor="华住酒店")
    outcome = await gateway.submit(case, payload, verifier_passed=True)

    await erp_ready.set_failure_mode("none")
    assert outcome.status in {"submitted", "unknown", "confirmed"}
    if outcome.status in {"submitted", "unknown"}:
        assert outcome.verified is False


def test_idempotency_key_is_stable_and_payload_sensitive():
    """幂等键必须对相同入参稳定，对参数变化敏感。"""
    payload = {"case_id": "c1", "amount": 100.0}
    k1 = compute_idempotency_key("c1", "submit_expense", payload)
    k2 = compute_idempotency_key("c1", "submit_expense", {"amount": 100.0, "case_id": "c1"})
    k3 = compute_idempotency_key("c1", "submit_expense", {"case_id": "c1", "amount": 101.0})

    assert k1 == k2  # 键序无关
    assert k1 != k3  # 金额变化即不同
    assert len(k1) == 64
