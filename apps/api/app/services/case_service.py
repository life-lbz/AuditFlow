"""案件相关服务层：详情聚合、缺失材料判断。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.models import (
    Approval,
    Case,
    Check,
    Decision,
    Document,
    ErpTransaction,
    Evidence,
)
from packages.core.state_machine import TERMINAL_STATES, allowed_events
from packages.documents.schema import DOCUMENT_TYPE_LABELS, FIELD_LABELS, REQUIRED_DOCUMENTS


async def build_case_detail(session: AsyncSession, case: Case) -> dict[str, Any]:
    """聚合案件详情（文档 §15.2：字段、证据、规则、决策、审批、ERP 一次返回）。

    数据库查询次数：1 次 Document（带 selectin 预取 pages + extracted_fields）
    + 1 次 Evidence + 1 次 Check + 1 次 Decision + 1 次 Approval + 1 次 ErpTransaction
    = 6 次；原实现在每个 document 上循环查 pages + fields，文档数 N 时为 2 + 3N + 2。
    """
    docs = (
        await session.execute(
            select(Document)
            .where(Document.case_id == case.id)
            .order_by(Document.created_at)
        )
    ).scalars().all()

    evidence_all = (
        await session.execute(
            select(Evidence).where(Evidence.case_id == case.id)
        )
    ).scalars().all()
    evidence_rows = [e for e in evidence_all if e.source_type != "policy"]
    policy_rows = [e for e in evidence_all if e.source_type == "policy"]

    checks = (
        await session.execute(
            select(Check).where(Check.case_id == case.id).order_by(Check.created_at)
        )
    ).scalars().all()

    decision = (
        await session.execute(
            select(Decision)
            .where(Decision.case_id == case.id)
            .order_by(Decision.created_at.desc())
        )
    ).scalars().first()

    approvals = (
        await session.execute(
            select(Approval)
            .where(Approval.case_id == case.id)
            .order_by(Approval.created_at)
        )
    ).scalars().all()

    erp_txn = (
        await session.execute(
            select(ErpTransaction)
            .where(ErpTransaction.case_id == case.id)
            .order_by(ErpTransaction.created_at.desc())
        )
    ).scalars().first()

    document_items: list[dict[str, Any]] = []
    extracted: dict[str, Any] = {}
    for doc in docs:
        document_items.append(
            {
                "id": doc.id,
                "type": doc.type,
                "type_label": DOCUMENT_TYPE_LABELS.get(doc.type, doc.type),
                "storage_key": doc.storage_key,
                "sha256": doc.sha256,
                "page_count": doc.page_count,
                "created_at": doc.created_at.isoformat() if doc.created_at else None,
                "pages": [
                    {
                        "page_no": p.page_no,
                        "ocr_text": p.ocr_text,
                        "image_key": p.image_key,
                        "image_url": f"/cases/{case.id}/documents/{doc.id}/pages/{p.page_no}/image"
                        if p.image_key
                        else None,
                    }
                    for p in doc.pages
                ],
                "fields": [
                    {
                        "field_name": f.field_name,
                        "field_label": FIELD_LABELS.get(f.field_name, f.field_name),
                        "value": f.value,
                        "confidence": f.confidence,
                        "page_no": f.page_no,
                        "bbox": f.bbox,
                    }
                    for f in doc.extracted_fields
                ],
            }
        )
        for f in doc.extracted_fields:
            extracted.setdefault(
                f.field_name,
                {
                    "value": f.value,
                    "confidence": f.confidence,
                    "label": FIELD_LABELS.get(f.field_name, f.field_name),
                    "document_id": doc.id,
                },
            )

    present_types = {d.type for d in docs}
    missing_documents = [
        DOCUMENT_TYPE_LABELS.get(t.value, t.value)
        for t in REQUIRED_DOCUMENTS.get(case.category, ())
        if t.value not in present_types
    ]

    return {
        "documents": document_items,
        "extracted_fields": extracted,
        "evidence": [
            {
                "id": e.id,
                "source_type": e.source_type,
                "source_id": e.source_id,
                "page_no": e.page_no,
                "bbox": e.bbox,
                "quote": e.quote,
                "score": e.score,
            }
            for e in evidence_rows
        ],
        "policy_evidence": [
            {
                "id": e.id,
                "source_type": e.source_type,
                "source_id": e.source_id,
                "page_no": e.page_no,
                "quote": e.quote,
                "score": e.score,
                "citation": f"{e.source_type}:{e.source_id}",
            }
            for e in policy_rows
        ],
        "checks": [
            {
                "id": c.id,
                "rule_name": c.rule_name,
                "rule_version": c.rule_version,
                "status": c.status,
                "severity": c.severity,
                "message": (c.result_json or {}).get("message", ""),
                "evidence_ids": (c.result_json or {}).get("evidence_ids", []),
                "input_json": c.input_json,
                "result_json": c.result_json,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in checks
        ],
        "decision": (
            {
                "id": decision.id,
                "decision": decision.decision,
                "confidence": decision.confidence,
                "risk_level": decision.risk_level,
                "reason": decision.reason,
                "evidence_ids": decision.evidence_ids or [],
                "created_at": decision.created_at.isoformat() if decision.created_at else None,
            }
            if decision
            else None
        ),
        "approvals": [
            {
                "id": a.id,
                "reviewer_id": a.reviewer_id,
                "action": a.action,
                "comment": a.comment,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in approvals
        ],
        "erp_transaction": (
            {
                "id": erp_txn.id,
                "erp_id": erp_txn.erp_id,
                "status": erp_txn.status,
                "idempotency_key": erp_txn.idempotency_key,
                "result_json": erp_txn.result_json,
            }
            if erp_txn
            else None
        ),
        "trace_url": f"/cases/{case.id}/trace",
        "allowed_events": [e.value for e in allowed_events(case.status)],
        "missing_documents": missing_documents,
    }


def is_terminal(status: str) -> bool:
    return status in {s.value for s in TERMINAL_STATES}
