"""管理员用户与邀请管理接口，仅 ADMIN 角色可访问。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_auth_service, get_db, require_admin
from app.api.schemas.auth import InviteUserPayload, UpdateUserStatusPayload
from app.api.utils import begin_write, finish_write
from app.api.v1.auth import user_data
from app.models.auth import User
from app.services.auth import AuthService

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users")
def list_users(
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """返回系统中全部用户与待激活邀请记录。"""
    return [user_data(user) for user in AuthService.list_users(session)]


@router.post("/users/invite", status_code=status.HTTP_201_CREATED)
def invite_user(
    payload: InviteUserPayload,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
) -> dict[str, Any]:
    """按邮箱定向邀请普通用户，并尝试发送邀请通知邮件。"""
    begin_write(session)
    try:
        user, invite_email_sent = auth_service.invite_user(
            session,
            admin,
            payload.email,
            display_name=payload.display_name,
        )
        finish_write(session, True)
        return {
            "user": user_data(user),
            "inviteEmailSent": invite_email_sent,
        }
    except Exception:
        finish_write(session, False)
        raise


@router.delete("/users/{user_id}/invite", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invite(
    user_id: str,
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> None:
    """撤销尚未首次登录激活的邀请记录。"""
    begin_write(session)
    try:
        AuthService.revoke_invite(session, user_id)
        finish_write(session, True)
    except Exception:
        finish_write(session, False)
        raise


@router.patch("/users/{user_id}/status")
def update_user_status(
    user_id: str,
    payload: UpdateUserStatusPayload,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
) -> dict[str, Any]:
    """禁用或重新启用普通用户，禁用时即时吊销其所有活跃会话。"""
    begin_write(session)
    try:
        user = auth_service.update_user_status(
            session,
            admin,
            user_id,
            payload.status,
        )
        finish_write(session, True)
        return {"user": user_data(user)}
    except Exception:
        finish_write(session, False)
        raise
