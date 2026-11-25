"""用户通知推送配置与一键测试接口路由。"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.schemas.notification import WeComNotificationTestRequest, WeComNotificationUpdate
from app.api.utils import begin_write, finish_write
from app.models.auth import User
from app.services.notifications.channels import PushChannel, WeComWebhookChannel
from app.services.notifications.service import NotificationConfigService

router = APIRouter(prefix="/notifications", tags=["notifications"])

_wecom_channel: PushChannel = WeComWebhookChannel()


def get_wecom_channel() -> PushChannel:
    """返回当前生效的企业微信推送通道实例（便于测试覆写）。"""
    return _wecom_channel


def set_wecom_channel(channel: PushChannel) -> None:
    """替换模块级企业微信推送通道实例（用于应用装配或测试注入）。"""
    global _wecom_channel
    _wecom_channel = channel


@router.get("/wecom")
def get_wecom_notification_config(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """读取当前登录用户的企业微信 Webhook 脱敏配置。"""
    return NotificationConfigService.get_wecom_config(session, current_user.id)


@router.put("/wecom")
def save_wecom_notification_config(
    payload: WeComNotificationUpdate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """保存、更新或清空当前登录用户的企业微信 Webhook 配置。"""
    begin_write(session)
    try:
        webhook_provided = "webhook_url" in payload.model_fields_set
        result = NotificationConfigService.save_wecom_config(
            session,
            current_user.id,
            enabled=payload.enabled,
            webhook_url=payload.webhook_url,
            webhook_url_provided=webhook_provided,
        )
        finish_write(session, True)
        return result
    except Exception:
        finish_write(session, False)
        raise


@router.post("/wecom/test")
async def test_wecom_notification(
    payload: WeComNotificationTestRequest | None = Body(default=None),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """向当前用户的企业微信群机器人发送一条测试消息以验证配置连通性。"""
    from datetime import UTC, datetime

    from app.services.notifications.formatter import format_test_markdown

    override_url = payload.webhook_url if payload is not None else None
    target_url = NotificationConfigService.resolve_test_webhook_url(
        session,
        current_user.id,
        webhook_url_override=override_url,
    )
    user_label = (current_user.display_name or "").strip() or current_user.email
    content = format_test_markdown(user_label=user_label, sent_at=datetime.now(UTC))
    channel = get_wecom_channel()
    await asyncio.to_thread(channel.send_markdown, target_url, content)
    return {"ok": True, "message": "测试消息已发送至企业微信群"}
