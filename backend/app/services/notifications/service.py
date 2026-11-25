"""用户通知渠道配置管理与一键测试发送服务。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.exceptions import ValidationError
from app.domain.time import format_beijing_iso
from app.models.auth import User
from app.models.notification import NotificationChannel

from .channels import (
    WECOM_CHANNEL_TYPE,
    PushChannel,
    mask_wecom_webhook_url,
    validate_wecom_webhook_url,
)
from .formatter import format_test_markdown


def _find_user_channel(
    session: Session,
    user_id: str,
    channel_type: str = WECOM_CHANNEL_TYPE,
) -> NotificationChannel | None:
    """按用户与渠道类型查询已持久化的通知渠道记录。"""
    return session.scalar(
        select(NotificationChannel).where(
            NotificationChannel.user_id == user_id,
            NotificationChannel.channel_type == channel_type,
        )
    )


def serialize_wecom_config(channel: NotificationChannel | None) -> dict[str, Any]:
    """将通知渠道 ORM 对象序列化为前端脱敏 DTO。"""
    if channel is None or not channel.webhook_url:
        return {
            "channelType": WECOM_CHANNEL_TYPE,
            "enabled": False,
            "configured": False,
            "maskedWebhookUrl": None,
            "updatedAt": format_beijing_iso(channel.updated_at) if channel is not None else None,
        }
    return {
        "channelType": channel.channel_type,
        "enabled": bool(channel.enabled),
        "configured": True,
        "maskedWebhookUrl": mask_wecom_webhook_url(channel.webhook_url),
        "updatedAt": format_beijing_iso(channel.updated_at),
    }


class NotificationConfigService:
    """管理当前用户的企业微信 Webhook 配置与连通性测试。"""

    @staticmethod
    def get_wecom_config(session: Session, user_id: str) -> dict[str, Any]:
        """只读获取当前用户的企业微信推送配置（不自动写库）。"""
        row = _find_user_channel(session, user_id, WECOM_CHANNEL_TYPE)
        return serialize_wecom_config(row)

    @staticmethod
    def save_wecom_config(
        session: Session,
        user_id: str,
        *,
        enabled: bool,
        webhook_url: str | None = None,
        webhook_url_provided: bool = False,
    ) -> dict[str, Any]:
        """在调用方写事务中保存、更新或清空当前用户的企业微信推送配置。"""
        row = _find_user_channel(session, user_id, WECOM_CHANNEL_TYPE)
        current_url = row.webhook_url if row is not None else None

        if webhook_url_provided:
            if webhook_url is None or not webhook_url.strip():
                next_url = None
                next_enabled = False
            else:
                next_url = validate_wecom_webhook_url(webhook_url)
                next_enabled = bool(enabled)
        else:
            next_url = current_url
            next_enabled = bool(enabled)

        if next_enabled and not next_url:
            raise ValidationError("启用止盈推送前请先填写有效的企业微信 Webhook 地址")

        now = datetime.now(UTC)
        if row is None:
            if next_url is None:
                return serialize_wecom_config(None)
            row = NotificationChannel(
                user_id=user_id,
                channel_type=WECOM_CHANNEL_TYPE,
                enabled=next_enabled,
                webhook_url=next_url,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
        else:
            row.webhook_url = next_url
            row.enabled = next_enabled
            row.updated_at = now

        session.flush()
        return serialize_wecom_config(row)

    @staticmethod
    def resolve_test_webhook_url(
        session: Session,
        user_id: str,
        *,
        webhook_url_override: str | None = None,
    ) -> str:
        """解析测试推送目标地址：显式传入时校验临时地址，否则读取用户已保存地址。"""
        if webhook_url_override is not None and webhook_url_override.strip():
            return validate_wecom_webhook_url(webhook_url_override)
        row = _find_user_channel(session, user_id, WECOM_CHANNEL_TYPE)
        if row is None or not row.webhook_url:
            raise ValidationError("请先填写或保存企业微信 Webhook 地址后再发送测试")
        return validate_wecom_webhook_url(row.webhook_url)

    @staticmethod
    def send_wecom_test(
        session: Session,
        user: User,
        *,
        channel: PushChannel,
        webhook_url_override: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """向当前用户的企微群发送一条中文测试卡片并验证连通性。"""
        target_url = NotificationConfigService.resolve_test_webhook_url(
            session,
            user.id,
            webhook_url_override=webhook_url_override,
        )
        sent_at = now or datetime.now(UTC)
        user_label = (user.display_name or "").strip() or user.email
        content = format_test_markdown(user_label=user_label, sent_at=sent_at)
        channel.send_markdown(target_url, content)
        return {"ok": True, "message": "测试消息已发送至企业微信群"}
