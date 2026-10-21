"""RBAC 权限矩阵（文档 §20.6）。

Action Gateway 使用服务级权限，不暴露给 Agent 直接调用（ADR-003）。
"""

from __future__ import annotations

import enum


class Role(str, enum.Enum):
    EMPLOYEE = "employee"
    REVIEWER = "reviewer"
    ADMIN = "admin"
    ACTION_GATEWAY = "action_gateway"  # 服务级身份


class Action(str, enum.Enum):
    CREATE_CASE = "create_case"
    VIEW_OWN_CASE = "view_own_case"
    VIEW_PENDING_CASES = "view_pending_cases"
    MODIFY_FIELDS = "modify_fields"
    APPROVE_REJECT = "approve_reject"
    MANAGE_POLICIES = "manage_policies"
    SUBMIT_ERP = "submit_erp"
    VIEW_TRACE = "view_trace"


# (Role, Action) -> bool；True=允许，False=禁止
_PERMISSION_MATRIX: dict[tuple[Role, Action], bool] = {
    (Role.EMPLOYEE, Action.CREATE_CASE): True,
    (Role.EMPLOYEE, Action.VIEW_OWN_CASE): True,
    (Role.EMPLOYEE, Action.VIEW_PENDING_CASES): False,
    (Role.EMPLOYEE, Action.MODIFY_FIELDS): True,   # 仅未提交，业务层再校验
    (Role.EMPLOYEE, Action.APPROVE_REJECT): False,
    (Role.EMPLOYEE, Action.MANAGE_POLICIES): False,
    (Role.EMPLOYEE, Action.SUBMIT_ERP): False,
    (Role.EMPLOYEE, Action.VIEW_TRACE): True,      # 仅自己的只读视图

    (Role.REVIEWER, Action.CREATE_CASE): False,
    (Role.REVIEWER, Action.VIEW_OWN_CASE): False,
    (Role.REVIEWER, Action.VIEW_PENDING_CASES): True,
    (Role.REVIEWER, Action.MODIFY_FIELDS): True,
    (Role.REVIEWER, Action.APPROVE_REJECT): True,
    (Role.REVIEWER, Action.MANAGE_POLICIES): False,
    (Role.REVIEWER, Action.SUBMIT_ERP): False,
    (Role.REVIEWER, Action.VIEW_TRACE): True,

    (Role.ADMIN, Action.CREATE_CASE): False,
    (Role.ADMIN, Action.VIEW_OWN_CASE): True,
    (Role.ADMIN, Action.VIEW_PENDING_CASES): True,
    (Role.ADMIN, Action.MODIFY_FIELDS): True,
    (Role.ADMIN, Action.APPROVE_REJECT): True,
    (Role.ADMIN, Action.MANAGE_POLICIES): True,
    (Role.ADMIN, Action.SUBMIT_ERP): False,
    (Role.ADMIN, Action.VIEW_TRACE): True,

    (Role.ACTION_GATEWAY, Action.CREATE_CASE): False,
    (Role.ACTION_GATEWAY, Action.VIEW_OWN_CASE): False,
    (Role.ACTION_GATEWAY, Action.VIEW_PENDING_CASES): False,
    (Role.ACTION_GATEWAY, Action.MODIFY_FIELDS): False,
    (Role.ACTION_GATEWAY, Action.APPROVE_REJECT): False,
    (Role.ACTION_GATEWAY, Action.MANAGE_POLICIES): False,
    (Role.ACTION_GATEWAY, Action.SUBMIT_ERP): True,  # 唯一可提交 ERP 的身份
    (Role.ACTION_GATEWAY, Action.VIEW_TRACE): True,  # 只读
}


class PermissionDenied(Exception):
    pass


def check_permission(role: Role, action: Action) -> bool:
    return _PERMISSION_MATRIX.get((role, action), False)


def require_permission(role: Role, action: Action) -> None:
    if not check_permission(role, action):
        raise PermissionDenied(f"{role.value} is not allowed to {action.value}")
