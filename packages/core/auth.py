"""轻量身份认证：HMAC-SHA256 签名 Token。

设计取舍：生产环境应接入企业 SSO / OIDC；本模块提供一个无第三方依赖、可离线运行的
签名 Token 实现，使「身份不能靠请求头伪造」这一底线要求可以落地。

用法：
    token = create_token("u_1001", "employee")
    payload = decode_token(token)     # 签名错误或过期抛 AuthError

命令行签发（token 模式下由管理员执行）：
    python -m packages.core.auth --subject reviewer_01 --role reviewer
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from .config import get_settings


class AuthError(Exception):
    """Token 缺失、格式错误、签名不匹配或已过期。"""


@dataclass(frozen=True)
class Identity:
    user_id: str
    role: str


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(body: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return _b64encode(digest)


def create_token(
    subject: str,
    role: str,
    *,
    ttl_seconds: int | None = None,
    secret: str | None = None,
    now: float | None = None,
) -> str:
    settings = get_settings()
    secret = secret or settings.auth_secret
    ttl = settings.auth_token_ttl_secs if ttl_seconds is None else ttl_seconds
    payload = {
        "sub": subject,
        "role": role,
        "exp": int((now if now is not None else time.time()) + ttl),
    }
    body = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return f"{body}.{_sign(body, secret)}"


def decode_token(token: str, *, secret: str | None = None, now: float | None = None) -> dict:
    settings = get_settings()
    secret = secret or settings.auth_secret
    if not token or token.count(".") != 1:
        raise AuthError("token 格式无效")
    body, signature = token.split(".", 1)
    if not hmac.compare_digest(signature, _sign(body, secret)):
        raise AuthError("token 签名无效")
    try:
        payload = json.loads(_b64decode(body))
    except Exception as exc:  # noqa: BLE001 - 任意解码失败都视为非法 token
        raise AuthError("token 载荷无法解析") from exc
    if not isinstance(payload, dict) or "sub" not in payload or "role" not in payload:
        raise AuthError("token 载荷缺少必需字段")
    if int(payload.get("exp", 0)) < int(now if now is not None else time.time()):
        raise AuthError("token 已过期")
    return payload


def _main() -> None:
    parser = argparse.ArgumentParser(description="签发 AuditFlow 访问令牌")
    parser.add_argument("--subject", required=True, help="用户标识，如 reviewer_01")
    parser.add_argument("--role", required=True, choices=["employee", "reviewer", "admin"])
    parser.add_argument("--ttl", type=int, default=None, help="有效期（秒），默认取 AUTH_TOKEN_TTL_SECS")
    args = parser.parse_args()
    print(create_token(args.subject, args.role, ttl_seconds=args.ttl))


if __name__ == "__main__":
    _main()
