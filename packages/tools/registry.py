"""工具注册表与执行器（文档 §11.1 / §11.2）。

职责：
- 声明工具契约（权限、输入输出 schema、超时、重试、幂等性）
- 校验参数（JSON Schema → Pydantic）
- 权限检查（Agent 不得直接执行高风险写操作）
- 超时与重试（只读工具可重试，写操作依赖幂等键）
- 落库 ToolCall 与 Trace
"""

from __future__ import annotations

import asyncio
import enum
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.models import Case, ToolCall
from ..core.tracing import EVENT_TOOL_COMPLETED, emit_trace, new_id
from ..gateway.base import ModelGateway


class ToolPermission(str, enum.Enum):
    READ_ONLY = "read_only"
    WRITE = "write"
    HIGH_RISK_WRITE = "high_risk_write"


class ToolError(Exception):
    """工具执行失败（业务可预期失败）。"""

    def __init__(self, message: str, error_code: str = "tool_error") -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass
class ToolSpec:
    """工具契约（文档 §11.2）。"""

    name: str
    description: str
    permission: ToolPermission = ToolPermission.READ_ONLY
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    timeout_ms: int = 10_000
    max_retries: int = 2
    idempotent: bool = True

    def as_dict(self) -> dict[str, Any]:
        """给模型看的工具清单。"""
        return {
            "name": self.name,
            "description": self.description,
            "permission": self.permission.value,
            "input_schema": self.input_schema,
            "timeout_ms": self.timeout_ms,
            "idempotent": self.idempotent,
        }


@dataclass
class ToolContext:
    """工具执行上下文。"""

    session: AsyncSession
    case: Case
    gateway: ModelGateway
    actor: str = "agent"
    trace_id: str | None = None
    approved_by: str | None = None  # 人工审批人（高风险写操作必需）
    verifier_passed: bool = False

    @property
    def case_id(self) -> str:
        return self.case.id


@dataclass
class ToolResult:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    error_code: str | None = None
    duration_ms: int = 0
    attempts: int = 1


class BaseTool(ABC):
    """工具基类：子类声明 spec 与 args_model，实现 run()。"""

    spec: ToolSpec
    args_model: type[BaseModel]

    def __init__(self) -> None:
        # 由 args_model 自动生成 JSON Schema（文档 §11.2：参数必须经 Schema 校验）
        if self.args_model is not None:
            self.spec.input_schema = self.args_model.model_json_schema()

    @abstractmethod
    async def run(self, args: BaseModel, ctx: ToolContext) -> dict[str, Any]:
        ...

    def check_permission(self, ctx: ToolContext) -> None:
        """工具级权限检查（文档 §11.3 高风险写操作前置条件）。"""
        if self.spec.permission == ToolPermission.HIGH_RISK_WRITE:
            if not ctx.verifier_passed:
                raise ToolError("Verifier 未通过，禁止执行高风险写操作", error_code="verifier_required")
            if not ctx.approved_by and not self._auto_submit_allowed(ctx):
                raise ToolError("缺少人工批准，禁止执行高风险写操作", error_code="approval_required")

    @staticmethod
    def _auto_submit_allowed(ctx: ToolContext) -> bool:
        return ctx.case.risk_level == "low"


class ToolRegistry:
    """工具注册与统一执行入口。"""

    def __init__(self, tools: list[BaseTool] | None = None) -> None:
        self._tools: dict[str, BaseTool] = {}
        for tool in tools if tools is not None else default_tools():
            self.register(tool)

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.spec.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self, allowed: list[str] | None = None) -> list[dict[str, Any]]:
        names = allowed if allowed is not None else self.names()
        return [self._tools[n].spec.as_dict() for n in names if n in self._tools]

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        ctx: ToolContext,
    ) -> ToolResult:
        tool = self._tools.get(name)
        started = time.perf_counter()
        call = ToolCall(
            id=new_id("tc"),
            case_id=ctx.case_id,
            tool_name=name,
            tool_version="1.0",
            arguments=arguments or {},
            status="pending",
        )
        ctx.session.add(call)

        if tool is None:
            await self._finish(ctx, call, None, "failed", "unknown_tool", int((time.perf_counter() - started) * 1000))
            return ToolResult(ok=False, error=f"未知工具 {name}", error_code="unknown_tool")

        # 参数校验
        try:
            args = tool.args_model.model_validate(arguments or {})
        except ValidationError as exc:
            call.status = "failed"
            call.error_code = "invalid_arguments"
            call.result = {"validation_error": exc.errors()[:5]}
            await ctx.session.flush()
            await self._trace(ctx, call, duration_ms=int((time.perf_counter() - started) * 1000))
            return ToolResult(ok=False, error="参数校验失败", error_code="invalid_arguments")

        # 权限检查
        try:
            tool.check_permission(ctx)
        except ToolError as exc:
            call.status = "failed"
            call.error_code = exc.error_code
            call.result = {"error": str(exc)}
            await ctx.session.flush()
            await self._trace(ctx, call, duration_ms=int((time.perf_counter() - started) * 1000))
            return ToolResult(ok=False, error=str(exc), error_code=exc.error_code)

        # 执行（超出 timeout 视为失败）
        attempts = 0
        last_error: Exception | None = None
        max_attempts = (tool.spec.max_retries + 1) if tool.spec.idempotent else 1
        while attempts < max_attempts:
            attempts += 1
            try:
                data = await asyncio.wait_for(
                    tool.run(args, ctx), timeout=tool.spec.timeout_ms / 1000.0
                )
                duration = int((time.perf_counter() - started) * 1000)
                call.status = "ok"
                call.result = data
                call.duration_ms = duration
                await ctx.session.flush()
                await self._trace(ctx, call, duration_ms=duration)
                return ToolResult(ok=True, data=data, duration_ms=duration, attempts=attempts)
            except asyncio.TimeoutError:
                last_error = ToolError("工具执行超时", error_code="tool_timeout")
            except ToolError as exc:
                last_error = exc
                if exc.error_code in {"approval_required", "verifier_required"}:
                    break
            except Exception as exc:  # noqa: BLE001 - 工具内部异常统一包装
                last_error = ToolError(f"工具执行异常: {exc}", error_code="tool_exception")

        duration = int((time.perf_counter() - started) * 1000)
        error_code = getattr(last_error, "error_code", "tool_error")
        call.status = "failed"
        call.error_code = error_code
        call.duration_ms = duration
        call.result = {"error": str(last_error)}
        await ctx.session.flush()
        await self._trace(ctx, call, duration_ms=duration, error_code=error_code)
        return ToolResult(
            ok=False,
            error=str(last_error) if last_error else "unknown error",
            error_code=error_code,
            duration_ms=duration,
            attempts=attempts,
        )

    async def _finish(
        self,
        ctx: ToolContext,
        call: ToolCall,
        result: dict[str, Any] | None,
        status: str,
        error_code: str | None,
        duration_ms: int,
    ) -> None:
        call.status = status
        call.result = result
        call.error_code = error_code
        call.duration_ms = duration_ms
        await ctx.session.flush()

    async def _trace(
        self,
        ctx: ToolContext,
        call: ToolCall,
        *,
        duration_ms: int,
        error_code: str | None = None,
    ) -> None:
        await emit_trace(
            ctx.session,
            case_id=ctx.case_id,
            trace_id=ctx.trace_id,
            event_type=EVENT_TOOL_COMPLETED,
            actor=ctx.actor,
            tool_name=call.tool_name,
            duration_ms=duration_ms,
            error_code=error_code,
            payload={
                "tool_call_id": call.id,
                "arguments": call.arguments,
                "status": call.status,
                "result": _compact(call.result),
            },
        )


def _compact(data: Any, limit: int = 800) -> Any:
    """Trace 中避免塞入过长结果。"""
    text = str(data)
    if len(text) <= limit:
        return data
    return text[:limit] + "...(truncated)"


def default_tools() -> list[BaseTool]:
    from .implementations import (
        CheckDuplicate,
        ExtractDocument,
        GetErpRecord,
        MatchBankTransaction,
        PreviewExpense,
        RequestHuman,
        SearchPolicy,
        SubmitExpense,
    )

    return [
        ExtractDocument(),
        SearchPolicy(),
        MatchBankTransaction(),
        CheckDuplicate(),
        GetErpRecord(),
        RequestHuman(),
        PreviewExpense(),
        SubmitExpense(),
    ]
