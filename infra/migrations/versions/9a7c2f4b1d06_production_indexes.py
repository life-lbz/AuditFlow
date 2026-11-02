"""production indexes

Revision ID: 9a7c2f4b1d06
Revises: 56f9de7c6555
Create Date: 2026-11-02

补充高频查询的复合索引：案件列表按员工+时间、轨迹/规则/审批按案件+时间。
"""

from __future__ import annotations

from alembic import op

revision = "9a7c2f4b1d06"
down_revision = "56f9de7c6555"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_cases_employee_created_at", "cases", ["employee_id", "created_at"], unique=False
    )
    op.create_index(
        "ix_trace_events_case_created_at",
        "trace_events",
        ["case_id", "created_at"],
        unique=False,
    )
    op.create_index("ix_checks_case_created_at", "checks", ["case_id", "created_at"], unique=False)
    op.create_index(
        "ix_approvals_case_created_at", "approvals", ["case_id", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_approvals_case_created_at", table_name="approvals")
    op.drop_index("ix_checks_case_created_at", table_name="checks")
    op.drop_index("ix_trace_events_case_created_at", table_name="trace_events")
    op.drop_index("ix_cases_employee_created_at", table_name="cases")
