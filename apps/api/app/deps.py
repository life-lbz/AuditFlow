"""通用依赖：身份解析与权限校验（文档 §20.6）。

两种模式（AUTH_MODE）：
- dev  ：信任 X-Role / X-User-Id 请求头，仅用于本地演示；
- token：从 Authorization: Bearer 解析 HMAC 签名 Token，请求头身份被完全忽略。

生产部署必须使用 token 模式；Token 由企业 SSO 或 `python -m packages.core.auth` 签发。
"""

from __future__ import annotations

from fastapi import Header, HTTPException

from packages.core.auth import AuthError, Identity, decode_token
from packages.core.config import get_settings
from packages.core.permissions import Action, Role, check_permission


def _resolve_identity(
    authorization: str | None,
    x_role: str,
    x_user_id: str,
) -> Identity:
    settings = get_settings()
    if (settings.auth_mode or "dev").strip().lower() == "token":
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(
                status_code=401,
                detail="缺少 Bearer Token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        try:
            payload = decode_token(authorization.split(" ", 1)[1].strip())
        except AuthError as exc:
            raise HTTPException(
                status_code=401,
                detail=f"身份校验失败: {exc}",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
        role_value = str(payload.get("role"))
        if role_value not in {r.value for r in Role}:
            raise HTTPException(status_code=401, detail=f"token 中的角色非法: {role_value}")
        return Identity(user_id=str(payload.get("sub")), role=role_value)

    try:
        role = Role(x_role)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"unknown role: {x_role}") from None
    return Identity(user_id=x_user_id, role=role.value)


def get_role(
    authorization: str | None = Header(default=None),
    x_role: str = Header(default="employee"),
    x_user_id: str = Header(default="anonymous"),
) -> Role:
    return Role(_resolve_identity(authorization, x_role, x_user_id).role)


def get_user_id(
    authorization: str | None = Header(default=None),
    x_role: str = Header(default="employee"),
    x_user_id: str = Header(default="anonymous"),
) -> str:
    return _resolve_identity(authorization, x_role, x_user_id).user_id


def require(role: Role, action: Action) -> None:
    if not check_permission(role, action):
        raise HTTPException(status_code=403, detail=f"{role.value} not allowed to {action.value}")
