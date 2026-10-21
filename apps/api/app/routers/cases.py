"""案件 API（文档 §15：创建、上传、运行、重试、详情、轨迹）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.config import get_settings
from packages.core.models import Case, CaseStatus, Document
from packages.core.permissions import Action
from packages.core.state_machine import CaseEvent, GuardFailedError, TransitionError, transition
from packages.core.storage import get_storage
from packages.core.tracing import EVENT_CASE_CREATED, emit_trace, list_trace
from packages.documents.parser import DocumentParseError
from packages.documents.service import ingest_document
from packages.gateway.factory import get_gateway
from packages.orchestrator.orchestrator import CaseOrchestrator

from ..deps import get_role, get_user_id, require
from ..schemas.case import CaseCreate, CaseDetail, CaseOut, RunRequest, RunResponse, TraceEventOut
from ..services.case_service import build_case_detail
from packages.core.db import get_session
from packages.core.tracing import new_id

router = APIRouter(tags=["cases"])

_ALLOWED_UPLOAD_EXT = {".txt", ".md", ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}


async def _get_case(session: AsyncSession, case_id: str) -> Case:
    case = await session.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"案件不存在: {case_id}")
    return case


@router.post("/cases", response_model=CaseOut, status_code=201)
async def create_case(
    payload: CaseCreate,
    session: AsyncSession = Depends(get_session),
    role=Depends(get_role),
    user_id=Depends(get_user_id),
) -> CaseOut:
    """创建案件（文档 §15.1）。"""
    require(role, Action.CREATE_CASE)

    case_id = new_id("case")
    trace_id = new_id("trace")
    case = Case(
        id=case_id,
        employee_id=payload.employee_id or user_id,
        category=payload.category,
        amount=payload.amount,
        description=payload.description,
        trace_id=trace_id,
    )
    session.add(case)
    await emit_trace(
        session,
        case_id=case_id,
        trace_id=trace_id,
        event_type=EVENT_CASE_CREATED,
        actor=user_id,
        payload={"input": payload.model_dump()},
    )
    await session.commit()
    await session.refresh(case)
    return CaseOut.model_validate(case)


@router.get("/cases", response_model=list[CaseOut])
async def list_cases(
    status: str | None = Query(default=None, description="按状态过滤"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
    role=Depends(get_role),
    user_id=Depends(get_user_id),
) -> list[CaseOut]:
    stmt = select(Case).order_by(Case.created_at.desc())
    if role.value == "employee":
        require(role, Action.VIEW_OWN_CASE)
        stmt = stmt.where(Case.employee_id == user_id)
    else:
        stmt = stmt.where(Case.status != "")  # 审核员/管理员可看全部
    if status:
        stmt = stmt.where(Case.status == status)
    rows = (await session.execute(stmt.limit(limit).offset(offset))).scalars().all()
    return [CaseOut.model_validate(c) for c in rows]


@router.get("/cases/{case_id}", response_model=CaseDetail)
async def get_case(
    case_id: str,
    session: AsyncSession = Depends(get_session),
    role=Depends(get_role),
    user_id=Depends(get_user_id),
) -> CaseDetail:
    case = await _get_case(session, case_id)
    if role.value == "employee" and case.employee_id != user_id:
        require(role, Action.VIEW_PENDING_CASES)  # 员工越权 → 403
    detail = await build_case_detail(session, case)
    return CaseDetail(**CaseOut.model_validate(case).model_dump(), **detail)


@router.post("/cases/{case_id}/documents", status_code=201)
async def upload_document(
    case_id: str,
    file: UploadFile = File(...),
    declared_type: str | None = Form(default=None),
    session: AsyncSession = Depends(get_session),
    role=Depends(get_role),
    user_id=Depends(get_user_id),
) -> dict[str, Any]:
    """上传文档并立即完成解析与字段抽取（文档 §7.1）。"""
    require(role, Action.MODIFY_FIELDS)
    case = await _get_case(session, case_id)
    if role.value == "employee" and case.employee_id != user_id:
        raise HTTPException(status_code=403, detail="无权向他人案件上传材料")

    filename = file.filename or "upload.bin"
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in _ALLOWED_UPLOAD_EXT:
        raise HTTPException(status_code=400, detail=f"不支持的文档格式: {suffix or '(无扩展名)'}")

    settings = get_settings()
    existing = (
        await session.execute(select(func.count(Document.id)).where(Document.case_id == case_id))
    ).scalar() or 0
    if existing >= settings.max_documents_per_case:
        raise HTTPException(
            status_code=409, detail=f"单案件文档数已达上限 {settings.max_documents_per_case}"
        )

    max_bytes = settings.max_upload_mb * 1024 * 1024
    data = await file.read(max_bytes + 1)
    if not data:
        raise HTTPException(status_code=400, detail="文件内容为空")
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail=f"文件超过 {settings.max_upload_mb}MB 限制")
    try:
        result = await ingest_document(
            session,
            get_gateway(),
            case_id=case_id,
            filename=filename,
            data=data,
            declared_type=declared_type,
            trace_id=case.trace_id,
        )
    except DocumentParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 推进状态机：CREATED → INGESTED / NEEDS_INFO → EXTRACTED
    await session.refresh(case)
    for event in (CaseEvent.DOCUMENTS_UPLOADED,):
        try:
            await transition(
                session,
                case_id,
                event,
                expected_version=case.state_version,
                actor=user_id,
                context={"documents": [result.document.id]},
            )
        except (TransitionError, GuardFailedError):
            pass  # 当前状态不接受该事件属正常（例如已进入 EXTRACTED）

    await session.commit()
    await session.refresh(case)
    return {
        "document_id": result.document.id,
        "document_type": result.document.type,
        "page_count": result.document.page_count,
        "ocr_backend": result.ocr_backend,
        "ocr_degraded": result.ocr_degraded,
        "fields": {
            k: {"value": v.value, "confidence": v.confidence} for k, v in (result.extraction.fields.items() if result.extraction else [])
        },
        "missing_fields": result.missing_fields,
        "errors": result.errors,
        "case_status": case.status,
    }


@router.get("/cases/{case_id}/documents/{document_id}/pages/{page_no}/image")
async def get_page_image(
    case_id: str,
    document_id: str,
    page_no: int,
    session: AsyncSession = Depends(get_session),
):
    """返回文档页面原图（证据查看器使用，文档 §16.2）。"""
    from packages.core.models import DocumentPage

    doc = await session.get(Document, document_id)
    if doc is None or doc.case_id != case_id:
        raise HTTPException(status_code=404, detail="文档不存在")
    page = (
        await session.execute(
            select(DocumentPage).where(
                DocumentPage.document_id == document_id, DocumentPage.page_no == page_no
            )
        )
    ).scalars().first()
    if page is None or not page.image_key:
        raise HTTPException(status_code=404, detail="页面图片不存在")
    try:
        content = await get_storage().get(page.image_key)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"读取图片失败: {exc}") from exc
    media_type = "image/png" if page.image_key.lower().endswith(".png") else "application/octet-stream"
    return Response(content=content, media_type=media_type)


@router.post("/cases/{case_id}/run", response_model=RunResponse)
async def run_case(
    case_id: str,
    payload: RunRequest | None = None,
    session: AsyncSession = Depends(get_session),
    role=Depends(get_role),
    user_id=Depends(get_user_id),
) -> RunResponse:
    """启动工作流（文档 §15：POST /cases/{id}/run）。"""
    require(role, Action.MODIFY_FIELDS)
    case = await _get_case(session, case_id)
    if role.value == "employee" and case.employee_id != user_id:
        raise HTTPException(status_code=403, detail="无权运行他人案件")

    if case.status in {CaseStatus.COMPLETED.value, CaseStatus.REJECTED.value, CaseStatus.ROLLED_BACK.value}:
        raise HTTPException(status_code=409, detail=f"案件已终结（{case.status}），不可重跑")
    if case.status in {CaseStatus.EXECUTING.value}:
        raise HTTPException(status_code=409, detail="案件正在执行 ERP，请稍后查询")

    options = payload or RunRequest()
    orchestrator = CaseOrchestrator(session, get_gateway(), mode=options.mode, actor=user_id)
    result = await orchestrator.run(case_id, auto_submit=options.auto_submit)

    return RunResponse(
        case_id=case_id,
        status=result.status,
        decision=result.decision,
        risk_level=result.risk_level,
        risk_score=result.risk_score,
        agent_used=result.agent_used,
        agent_triggers=result.agent_triggers,
        rule_failures=result.rule_failures,
        verifier_status=result.verifier_status,
        verifier_reason=result.verifier_reason,
        needs_approval=result.needs_approval,
        auto_submit_eligible=result.auto_submit_eligible,
        erp=result.erp,
        transitions=result.transitions,
        errors=result.errors,
    )


@router.post("/cases/{case_id}/retry", response_model=RunResponse)
async def retry_case(
    case_id: str,
    payload: RunRequest | None = None,
    session: AsyncSession = Depends(get_session),
    role=Depends(get_role),
    user_id=Depends(get_user_id),
) -> RunResponse:
    """从检查点重试（文档 §15：POST /cases/{id}/retry）。

    适用于 FAILED / ESCALATED / NEEDS_INFO 等中间态：状态回退后按当前快照继续推进。
    """
    require(role, Action.MODIFY_FIELDS)
    case = await _get_case(session, case_id)

    if case.status in {
        CaseStatus.NEEDS_INFO.value,
        CaseStatus.EXTRACTED.value,
        CaseStatus.EVIDENCE_READY.value,
        CaseStatus.VALIDATED.value,
        CaseStatus.DECIDED.value,
        CaseStatus.VERIFIED.value,
        CaseStatus.ESCALATED.value,
    }:
        # 回到可继续编排的状态：NEEDS_INFO 补材料后重新抽取
        if case.status == CaseStatus.NEEDS_INFO.value:
            await transition(
                session,
                case_id,
                CaseEvent.DOCUMENTS_UPLOADED,
                expected_version=case.state_version,
                actor=user_id,
                context={"documents": [d.id for d in case.documents]},
            )
        await session.commit()
        await session.refresh(case)
    elif case.status != CaseStatus.CREATED.value:
        raise HTTPException(status_code=409, detail=f"当前状态 {case.status} 不支持重试")

    options = payload or RunRequest()
    orchestrator = CaseOrchestrator(session, get_gateway(), mode=options.mode, actor=user_id)
    result = await orchestrator.run(case_id, auto_submit=options.auto_submit)
    return RunResponse(
        case_id=case_id,
        status=result.status,
        decision=result.decision,
        risk_level=result.risk_level,
        risk_score=result.risk_score,
        agent_used=result.agent_used,
        agent_triggers=result.agent_triggers,
        rule_failures=result.rule_failures,
        verifier_status=result.verifier_status,
        verifier_reason=result.verifier_reason,
        needs_approval=result.needs_approval,
        auto_submit_eligible=result.auto_submit_eligible,
        erp=result.erp,
        transitions=result.transitions,
        errors=result.errors,
    )


@router.get("/cases/{case_id}/trace", response_model=list[TraceEventOut])
async def get_trace(
    case_id: str,
    session: AsyncSession = Depends(get_session),
    role=Depends(get_role),
) -> list[TraceEventOut]:
    require(role, Action.VIEW_TRACE)
    events = await list_trace(session, case_id)
    return [TraceEventOut.model_validate(e) for e in events]


@router.get("/cases/{case_id}/replay")
async def replay_case(
    case_id: str,
    session: AsyncSession = Depends(get_session),
    role=Depends(get_role),
) -> dict[str, Any]:
    """确定性回放（文档 §18.2）：按顺序重建案件处理过程，不产生任何副作用。"""
    require(role, Action.VIEW_TRACE)
    case = await _get_case(session, case_id)
    events = await list_trace(session, case_id)

    timeline: list[dict[str, Any]] = []
    for idx, event in enumerate(events, start=1):
        payload = event.payload_json or {}
        item: dict[str, Any] = {
            "seq": idx,
            "at": event.created_at.isoformat() if event.created_at else None,
            "event_type": event.event_type,
            "actor": event.actor,
            "duration_ms": event.duration_ms,
        }
        if event.event_type == "state_transition":
            item["summary"] = f"{payload.get('from')} → {payload.get('to')}（{payload.get('event')}）"
            item["transition"] = payload
        elif event.event_type == "document_extracted":
            item["summary"] = f"抽取 {len(payload.get('fields') or {})} 个字段"
            item["fields"] = payload.get("fields")
        elif event.event_type == "policy_retrieved":
            item["summary"] = f"命中 {len(payload.get('hits') or [])} 条制度条款"
            item["citations"] = payload.get("citations")
        elif event.event_type == "rule_evaluated":
            item["summary"] = f"{payload.get('rule_name')}={payload.get('status')}：{payload.get('message')}"
        elif event.event_type == "decision_made":
            dec = payload.get("decision") or {}
            item["summary"] = f"决策 {dec.get('decision')}（风险 {dec.get('risk_level')}）"
            item["decision"] = dec
        elif event.event_type == "verifier_finished":
            item["summary"] = f"验证 {payload.get('status')}：{payload.get('reason')}"
        elif event.event_type == "erp_executed":
            item["summary"] = f"ERP {payload.get('phase')}（{payload.get('status')}）"
        elif event.event_type == "tool_completed":
            item["summary"] = f"工具 {payload.get('tool_name') if 'tool_name' in payload else event.tool_name} {payload.get('status')}"
        else:
            item["summary"] = event.event_type
        timeline.append(item)

    return {
        "case_id": case_id,
        "final_status": case.status,
        "risk_level": case.risk_level,
        "event_count": len(events),
        "timeline": timeline,
        "note": "回放基于已持久化的 Trace 与模型/工具录制结果，不触发新的 ERP 副作用",
    }
