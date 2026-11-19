"""认证接口：发送邮箱验证码、验证码登录、查询当前用户与退出登录。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_auth_service, get_current_user, get_db
from app.api.schemas.auth import RequestCodePayload, VerifyCodePayload
from app.api.utils import begin_write, finish_write
from app.domain.exceptions import (
    InvalidVerificationCodeError,
    VerificationCodeAttemptsExceededError,
)
from app.models.auth import User
from app.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def _dt_utc(value: datetime | None) -> str | None:
    """将数据库时间统一转换为带 UTC 时区的 ISO 字符串。"""
    if value is None:
        return None
    normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return normalized.isoformat()


def user_data(user: User) -> dict[str, Any]:
    """序列化用户公开资料与角色状态。"""
    return {
        "id": user.id,
        "email": user.email,
        "displayName": user.display_name,
        "role": user.role,
        "status": user.status,
        "isDefaultAdmin": user.is_default_admin,
        "lastLoginAt": _dt_utc(user.last_login_at),
        "createdAt": _dt_utc(user.created_at),
    }


@router.post("/request-code")
def request_code(
    payload: RequestCodePayload,
    session: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
) -> dict[str, Any]:
    """向已受邀或已激活邮箱发送 6 位登录验证码。"""
    begin_write(session)
    try:
        _code, expires_in, cooldown = auth_service.request_login_code(session, payload.email)
        finish_write(session, True)
        return {
            "sent": True,
            "expiresInSeconds": expires_in,
            "cooldownSeconds": cooldown,
        }
    except Exception:
        finish_write(session, False)
        raise


@router.post("/verify-code")
def verify_code(
    payload: VerifyCodePayload,
    request: Request,
    response: Response,
    session: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
) -> dict[str, Any]:
    """校验邮箱验证码并下发 30 天有效期的 HttpOnly 会话 Cookie。"""
    begin_write(session)
    try:
        user, raw_token, _expires_at = auth_service.verify_login_code(
            session,
            payload.email,
            payload.code,
            user_agent=request.headers.get("user-agent"),
            ip_address=request.client.host if request.client else None,
        )
        finish_write(session, True)
    except (InvalidVerificationCodeError, VerificationCodeAttemptsExceededError):
        # 持久化失败尝试次数，确保 5 次输错锁定真实生效。
        finish_write(session, True)
        raise
    except Exception:
        finish_write(session, False)
        raise

    max_age = auth_service.settings.auth_session_days * 24 * 60 * 60
    response.set_cookie(
        key=auth_service.settings.auth_cookie_name,
        value=raw_token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=auth_service.settings.auth_cookie_secure,
        path="/",
    )
    return {"user": user_data(user)}


@router.get("/me")
def read_me(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """返回当前已登录用户资料。"""
    return {"user": user_data(current_user)}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    session: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
) -> None:
    """吊销当前服务端会话并清除浏览器登录 Cookie。"""
    raw_token = request.cookies.get(auth_service.settings.auth_cookie_name)
    begin_write(session)
    try:
        auth_service.revoke_session(session, raw_token)
        finish_write(session, True)
    except Exception:
        finish_write(session, False)
        raise
    response.delete_cookie(
        key=auth_service.settings.auth_cookie_name,
        path="/",
        httponly=True,
        samesite="lax",
        secure=auth_service.settings.auth_cookie_secure,
    )
