"""Action Gateway：所有 ERP 副作用的唯一入口（文档 §11.3、§ADR-003）。

强制约束：
1. Verifier 已通过。
2. 风险允许自动执行，或人工已批准。
3. 参数通过 Schema 校验（由工具层完成，这里再做业务校验）。
4. 幂等键不存在重复。
5. 先 preview，再 submit。
6. submit 后必须查询真实状态（不能把 HTTP 200 当业务成功）。
7. 失败时记录补偿动作。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.models import Case, ErpTransaction
from ..core.tracing import EVENT_ERP_EXECUTED, emit_trace, new_id
from .client import ErpClient, ErpClientError

SUBMIT_ACTION = "submit_expense"


@dataclass
class GatewayOutcome:
    status: str  # confirmed / submitted / unknown / failed / duplicate_prevented / rejected
    erp_id: str | None = None
    transaction_id: str | None = None
    idempotency_key: str = ""
    verified: bool = False
    snapshot: dict[str, Any] = field(default_factory=dict)
    compensation: str | None = None
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "erp_id": self.erp_id,
            "transaction_id": self.transaction_id,
            "idempotency_key": self.idempotency_key,
            "verified": self.verified,
            "snapshot": self.snapshot,
            "compensation": self.compensation,
            "message": self.message,
        }


def compute_idempotency_key(case_id: str, action: str, payload: dict[str, Any]) -> str:
    """idempotency_key = sha256(case_id + action + canonical_payload)（文档 §11.3）。"""
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    raw = f"{case_id}|{action}|{canonical}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ActionGateway:
    def __init__(self, session: AsyncSession, client: ErpClient | None = None) -> None:
        self.session = session
        self.client = client or ErpClient()

    # ------------------------------------------------------------------
    def build_payload(self, case: Case, *, vendor: str = "", description: str = "") -> dict[str, Any]:
        return {
            "case_id": case.id,
            "employee_id": case.employee_id,
            "category": case.category,
            "amount": float(case.amount),
            "vendor": vendor or "unknown",
            "description": description or case.description or "",
        }

    def preview(self, case: Case, payload: dict[str, Any]) -> dict[str, Any]:
        """预览：不产生任何 ERP 副作用，只回显将要提交的内容与幂等键。"""
        errors: list[str] = []
        if not payload.get("case_id"):
            errors.append("缺少 case_id")
        if not payload.get("employee_id"):
            errors.append("缺少 employee_id")
        amount = payload.get("amount")
        if not isinstance(amount, (int, float)) or amount <= 0:
            errors.append("金额必须为正数")
        if payload.get("category") not in {"travel", "meal", "office_supplies"}:
            errors.append(f"非法报销类型 {payload.get('category')}")

        return {
            "valid": not errors,
            "errors": errors,
            "payload": payload,
            "idempotency_key": compute_idempotency_key(case.id, SUBMIT_ACTION, payload),
        }

    # ------------------------------------------------------------------
    async def submit(
        self,
        case: Case,
        payload: dict[str, Any],
        *,
        approved_by: str | None = None,
        verifier_passed: bool = False,
        trace_id: str | None = None,
    ) -> GatewayOutcome:
        started_preview = self.preview(case, payload)
        idem = started_preview["idempotency_key"]

        # 前置条件：Verifier 通过 + 批准
        if not verifier_passed:
            return GatewayOutcome(status="rejected", message="Verifier 未通过，拒绝提交", idempotency_key=idem)
        if not approved_by and case.risk_level != "low":
            return GatewayOutcome(
                status="rejected",
                message="高风险案件缺少人工批准，拒绝提交",
                idempotency_key=idem,
            )
        if not started_preview["valid"]:
            return GatewayOutcome(
                status="rejected",
                message="payload 校验失败: " + "; ".join(started_preview["errors"]),
                idempotency_key=idem,
            )

        # 幂等：已存在的事务直接返回结果，不重复提交
        existing = await self._find_transaction(idem)
        if existing is not None:
            if existing.status == "confirmed":
                await self._trace(case.id, trace_id, "duplicate_prevented", existing, payload)
                return GatewayOutcome(
                    status="duplicate_prevented",
                    erp_id=existing.erp_id,
                    transaction_id=existing.id,
                    idempotency_key=idem,
                    verified=True,
                    snapshot=(existing.result_json or {}),
                    message="幂等键已存在且已确认，跳过重复提交",
                )
            if existing.erp_id:
                # 上次提交结果不明（如超时）→ 只查不写（文档 §14.3）
                outcome = await self._verify_existing(case, existing, payload, trace_id)
                if outcome.status in {"confirmed", "submitted"}:
                    return outcome

        payload_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()

        txn = existing or ErpTransaction(
            id=new_id("txn"),
            case_id=case.id,
            status="pending",
            idempotency_key=idem,
            payload_hash=payload_hash,
        )
        if existing is None:
            self.session.add(txn)
            await self.session.flush()

        # 1) 创建单据（幂等键随请求发送；超时后重试可拿回同一单据）
        erp_record: dict[str, Any] | None = None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                erp_record = await self.client.create_expense(payload, idem)
                break
            except ErpClientError as exc:
                last_error = exc
                # 409 duplicate_submit：ERP 侧已有该单据，取出 erp_id 后继续核对
                existing_id = (exc.payload or {}).get("existing_erp_id")
                if exc.status_code == 409 and existing_id:
                    erp_record = {"erp_id": existing_id, "status": "draft"}
                    break
                if exc.status_code in {422, 400}:
                    txn.status = "failed"
                    txn.result_json = {"error": str(exc), "payload": exc.payload}
                    await self.session.flush()
                    await self._trace(case.id, trace_id, "failed", txn, payload, error_code="invalid_payload")
                    return GatewayOutcome(
                        status="failed",
                        transaction_id=txn.id,
                        idempotency_key=idem,
                        compensation="none_required",
                        message=f"ERP 拒绝 payload: {exc}",
                    )

        if erp_record is None:
            txn.status = "failed"
            txn.result_json = {"error": str(last_error)}
            await self.session.flush()
            await self._trace(case.id, trace_id, "failed", txn, payload, error_code="erp_unreachable")
            return GatewayOutcome(
                status="failed",
                transaction_id=txn.id,
                idempotency_key=idem,
                compensation="none_required",
                message=f"ERP 创建单据失败：{last_error}",
            )

        erp_id = erp_record.get("erp_id")
        txn.erp_id = erp_id
        txn.status = "submitted"
        txn.result_json = {"create_response": erp_record}
        await self.session.flush()

        # 2) 提交单据
        if erp_record.get("status") != "submitted" and erp_id:
            try:
                await self.client.submit_expense(erp_id)
            except ErpClientError as exc:
                # 提交失败但单据已创建 → 记录补偿动作（文档 §11.3-7）
                txn.status = "failed"
                txn.result_json = {**(txn.result_json or {}), "submit_error": str(exc)}
                await self.session.flush()
                await self._trace(case.id, trace_id, "failed", txn, payload, error_code="submit_failed")
                return GatewayOutcome(
                    status="failed",
                    erp_id=erp_id,
                    transaction_id=txn.id,
                    idempotency_key=idem,
                    compensation=f"void_draft_expense:{erp_id}",
                    message=f"ERP 提交失败：{exc}",
                )

        # 3) 查询真实状态并比对（不把 HTTP 200 当成功）
        return await self._verify_existing(case, txn, payload, trace_id)

    # ------------------------------------------------------------------
    async def query(self, erp_id: str) -> dict[str, Any]:
        return await self.client.get_expense(erp_id)

    async def history(self, erp_id: str) -> dict[str, Any]:
        return await self.client.get_history(erp_id)

    # ------------------------------------------------------------------
    async def _verify_existing(
        self,
        case: Case,
        txn: ErpTransaction,
        payload: dict[str, Any],
        trace_id: str | None,
    ) -> GatewayOutcome:
        """查询 ERP 真实状态并做字段级比对（文档 §14.4）。"""
        erp_id = txn.erp_id
        if not erp_id:
            txn.status = "failed"
            await self.session.flush()
            return GatewayOutcome(
                status="failed",
                transaction_id=txn.id,
                idempotency_key=txn.idempotency_key,
                compensation="none_required",
                message="缺少 erp_id，无法核对",
            )

        try:
            record = await self.client.get_expense(erp_id)
        except ErpClientError as exc:
            txn.status = "unknown"
            await self.session.flush()
            await self._trace(case.id, trace_id, "unknown", txn, payload, error_code="erp_query_failed")
            return GatewayOutcome(
                status="unknown",
                erp_id=erp_id,
                transaction_id=txn.id,
                idempotency_key=txn.idempotency_key,
                message=f"ERP 状态查询失败，需人工核查：{exc}",
            )

        mismatches = self._compare(payload, record)
        if mismatches:
            txn.status = "unknown"
            txn.result_json = {**(txn.result_json or {}), "snapshot": record, "mismatches": mismatches}
            await self.session.flush()
            await self._trace(case.id, trace_id, "mismatch", txn, payload, error_code="erp_mismatch")
            return GatewayOutcome(
                status="unknown",
                erp_id=erp_id,
                transaction_id=txn.id,
                idempotency_key=txn.idempotency_key,
                snapshot=record,
                message="ERP 返回与提交参数不一致：" + "; ".join(mismatches),
            )

        status = str(record.get("status") or "")
        if status in {"submitted", "approved"}:
            txn.status = "confirmed"
            txn.result_json = {**(txn.result_json or {}), "snapshot": record}
            await self.session.flush()
            await self._trace(case.id, trace_id, "confirmed", txn, payload)
            return GatewayOutcome(
                status="confirmed",
                erp_id=erp_id,
                transaction_id=txn.id,
                idempotency_key=txn.idempotency_key,
                verified=True,
                snapshot=record,
                message=f"ERP 单据已确认（状态 {status}）",
            )

        # 已创建但状态尚未可见（delayed_state）
        txn.status = "submitted"
        txn.result_json = {**(txn.result_json or {}), "snapshot": record}
        await self.session.flush()
        await self._trace(case.id, trace_id, "submitted", txn, payload)
        return GatewayOutcome(
            status="submitted",
            erp_id=erp_id,
            transaction_id=txn.id,
            idempotency_key=txn.idempotency_key,
            verified=False,
            snapshot=record,
            message=f"ERP 单据已创建但状态未进入审批（当前 {status}），需复核",
        )

    @staticmethod
    def _compare(payload: dict[str, Any], record: dict[str, Any]) -> list[str]:
        mismatches: list[str] = []
        if record.get("case_id") and record["case_id"] != payload.get("case_id"):
            mismatches.append(f"case_id 不一致: {record['case_id']} != {payload.get('case_id')}")
        rec_amount = record.get("amount")
        if rec_amount is not None and abs(float(rec_amount) - float(payload.get("amount", 0))) > 0.01:
            mismatches.append(f"金额不一致: {rec_amount} != {payload.get('amount')}")
        if record.get("vendor") and payload.get("vendor") and record["vendor"] != payload["vendor"]:
            mismatches.append(f"供应商不一致: {record['vendor']} != {payload['vendor']}")
        return mismatches

    async def _find_transaction(self, idem: str) -> ErpTransaction | None:
        result = await self.session.execute(
            select(ErpTransaction).where(ErpTransaction.idempotency_key == idem)
        )
        return result.scalars().first()

    async def _trace(
        self,
        case_id: str,
        trace_id: str | None,
        phase: str,
        txn: ErpTransaction,
        payload: dict[str, Any],
        error_code: str | None = None,
    ) -> None:
        await emit_trace(
            self.session,
            case_id=case_id,
            trace_id=trace_id,
            event_type=EVENT_ERP_EXECUTED,
            actor="action_gateway",
            error_code=error_code,
            payload={
                "phase": phase,
                "transaction_id": txn.id,
                "erp_id": txn.erp_id,
                "status": txn.status,
                "idempotency_key": txn.idempotency_key,
                "payload": payload,
                "result": txn.result_json,
            },
        )
