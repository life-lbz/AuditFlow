"""Mock ERP 服务（文档 §14）。

模拟真实企业系统的副作用、延迟和异常：
- POST /expenses            创建报销单（支持 Idempotency-Key 幂等）
- POST /expenses/{id}/submit 提交审批
- GET  /expenses/{id}       查询状态
- GET  /expenses/{id}/history 历史
- POST /admin/failure-mode  注入故障模式
"""

from __future__ import annotations

import asyncio
import time
import uuid
from enum import Enum

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


class FailureMode(str, Enum):
    NONE = "none"
    TIMEOUT_AFTER_COMMIT = "timeout_after_commit"  # 数据已写入但响应超时
    DUPLICATE_SUBMIT = "duplicate_submit"          # 重复请求返回不同结果
    SERVER_ERROR = "server_error"                  # 500
    DELAYED_STATE = "delayed_state"                # 提交成功但状态延迟可见
    INVALID_PAYLOAD = "invalid_payload"            # 字段校验失败


class ExpenseStatus(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"


class ExpenseCreate(BaseModel):
    case_id: str
    employee_id: str
    category: str
    amount: float = Field(gt=0)
    vendor: str = ""
    description: str = ""


class FailureModeRequest(BaseModel):
    mode: FailureMode = FailureMode.NONE
    delay_seconds: float = 15.0       # timeout_after_commit 时的挂起时长
    state_delay_seconds: float = 5.0  # delayed_state 时的状态可见延迟


class ExpenseRecord(BaseModel):
    erp_id: str
    case_id: str
    employee_id: str
    category: str
    amount: float
    vendor: str
    description: str
    status: ExpenseStatus
    idempotency_key: str | None
    created_at: float
    submitted_at: float | None = None
    history: list[dict] = []


app = FastAPI(title="AuditFlow Mock ERP", version="1.0")

# 内存存储（MVP；重启即清空）
_EXPENSES: dict[str, ExpenseRecord] = {}
_IDEMPOTENCY_INDEX: dict[str, str] = {}  # idempotency_key -> erp_id
_FAILURE = FailureModeRequest()


@app.middleware("http")
async def failure_mode_middleware(request: Request, call_next):
    """故障注入中间件：对 /expenses 写路径生效。"""
    if request.url.path.startswith("/admin") or request.method == "GET":
        return await call_next(request)

    mode = _FAILURE.mode
    if mode == FailureMode.SERVER_ERROR:
        return JSONResponse(status_code=500, content={"detail": "mock erp internal error (injected)"})
    if mode == FailureMode.TIMEOUT_AFTER_COMMIT:
        # 关键：先放行让数据真正写入，再挂起响应制造"超时但已提交"
        response = await call_next(request)
        await asyncio.sleep(_FAILURE.delay_seconds)
        return response
    return await call_next(request)


@app.post("/expenses", status_code=201)
async def create_expense(payload: ExpenseCreate, idempotency_key: str | None = Header(default=None)):
    if _FAILURE.mode == FailureMode.INVALID_PAYLOAD:
        raise HTTPException(status_code=422, detail={"field": "amount", "error": "invalid (injected)"})

    # 幂等：同一 key 重复创建返回同一单据（文档 §14.5「重复提交」场景的标准行为）
    if idempotency_key and idempotency_key in _IDEMPOTENCY_INDEX:
        existing = _EXPENSES[_IDEMPOTENCY_INDEX[idempotency_key]]
        if _FAILURE.mode == FailureMode.DUPLICATE_SUBMIT:
            # 模拟糟糕 ERP：重复请求返回 409 提示已存在
            raise HTTPException(status_code=409, detail={"existing_erp_id": existing.erp_id})
        return existing

    record = ExpenseRecord(
        erp_id=f"erp_{uuid.uuid4().hex[:12]}",
        case_id=payload.case_id,
        employee_id=payload.employee_id,
        category=payload.category,
        amount=payload.amount,
        vendor=payload.vendor,
        description=payload.description,
        status=ExpenseStatus.DRAFT,
        idempotency_key=idempotency_key,
        created_at=time.time(),
        history=[{"at": time.time(), "event": "created"}],
    )
    _EXPENSES[record.erp_id] = record
    if idempotency_key:
        _IDEMPOTENCY_INDEX[idempotency_key] = record.erp_id
    return record


@app.post("/expenses/{erp_id}/submit")
async def submit_expense(erp_id: str):
    record = _EXPENSES.get(erp_id)
    if record is None:
        raise HTTPException(status_code=404, detail="expense not found")
    if record.status == ExpenseStatus.SUBMITTED:
        return record  # 重复提交幂等返回
    record.status = ExpenseStatus.SUBMITTED
    record.submitted_at = time.time()
    record.history.append({"at": time.time(), "event": "submitted"})
    return record


@app.get("/expenses/{erp_id}")
async def get_expense(erp_id: str):
    record = _EXPENSES.get(erp_id)
    if record is None:
        raise HTTPException(status_code=404, detail="expense not found")

    # delayed_state：提交后一段时间内查询仍显示 draft
    if (
        _FAILURE.mode == FailureMode.DELAYED_STATE
        and record.status == ExpenseStatus.SUBMITTED
        and record.submitted_at is not None
        and time.time() - record.submitted_at < _FAILURE.state_delay_seconds
    ):
        snapshot = record.model_copy(deep=True)
        snapshot.status = ExpenseStatus.DRAFT
        return snapshot
    return record


@app.get("/expenses/{erp_id}/history")
async def get_expense_history(erp_id: str):
    record = _EXPENSES.get(erp_id)
    if record is None:
        raise HTTPException(status_code=404, detail="expense not found")
    return {"erp_id": erp_id, "history": record.history}


@app.post("/admin/failure-mode")
async def set_failure_mode(req: FailureModeRequest):
    global _FAILURE
    _FAILURE = req
    return {"mode": _FAILURE.mode, "delay_seconds": _FAILURE.delay_seconds,
            "state_delay_seconds": _FAILURE.state_delay_seconds}


@app.get("/admin/state")
async def admin_state():
    """调试/测试用：查看全部单据与当前故障模式。"""
    return {
        "failure_mode": _FAILURE.mode,
        "expense_count": len(_EXPENSES),
        "expenses": list(_EXPENSES.values()),
    }


@app.post("/admin/reset")
async def admin_reset():
    _EXPENSES.clear()
    _IDEMPOTENCY_INDEX.clear()
    await set_failure_mode(FailureModeRequest())
    return {"ok": True}
