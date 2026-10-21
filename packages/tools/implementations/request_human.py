"""写工具：创建人工任务（文档 §11.1 request_human）。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..registry import BaseTool, ToolContext, ToolPermission, ToolSpec
from ...core.tracing import new_id


class RequestHumanArgs(BaseModel):
    reason: str = Field(min_length=1, description="转人工的原因说明")
    priority: str = Field(default="normal", description="优先级：low / normal / high")
    suggested_checks: list[str] = Field(default_factory=list, description="建议审核员重点核对的项")


class RequestHuman(BaseTool):
    spec = ToolSpec(
        name="request_human",
        description="创建人工审核任务，把案件转交财务审核员处理",
        permission=ToolPermission.WRITE,
        timeout_ms=5_000,
        max_retries=1,
        idempotent=True,
    )
    args_model = RequestHumanArgs

    async def run(self, args: RequestHumanArgs, ctx: ToolContext) -> dict:
        priority = args.priority if args.priority in {"low", "normal", "high"} else "normal"
        task_id = new_id("hitl")
        await ctx.session.flush()
        return {
            "task_id": task_id,
            "case_id": ctx.case_id,
            "status": "pending_review",
            "priority": priority,
            "reason": args.reason,
            "suggested_checks": args.suggested_checks,
            "queue": "finance_review",
        }
