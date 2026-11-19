"""认证与管理员用户管理请求模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.api.schemas.common import APIModel


class RequestCodePayload(APIModel):
    """发送登录验证码请求体。"""

    email: str = Field(min_length=3, max_length=255)


class VerifyCodePayload(APIModel):
    """提交邮箱验证码登录请求体。"""

    email: str = Field(min_length=3, max_length=255)
    code: str = Field(min_length=1, max_length=16)


class InviteUserPayload(APIModel):
    """管理员按邮箱邀请新用户请求体。"""

    email: str = Field(min_length=3, max_length=255)
    display_name: str | None = Field(default=None, max_length=128)


class UpdateUserStatusPayload(APIModel):
    """管理员更新用户启用/禁用状态请求体。"""

    status: Literal["ACTIVE", "DISABLED"]
