"""用户通知渠道配置与止盈推送去重记录持久化模型。"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, ExactDecimal


def _uuid() -> str:
    """生成标准 UUID 字符串主键。"""
    return str(uuid4())


def utcnow() -> datetime:
    """返回当前 UTC 时间戳。"""
    return datetime.now(UTC)


class NotificationChannel(Base):
    """按用户与渠道类型唯一隔离的外部通知渠道配置（首期支持企业微信群机器人 Webhook）。"""

    __tablename__ = "notification_channels"
    __table_args__ = (
        UniqueConstraint("user_id", "channel_type", name="uq_notification_channels_user_type"),
        Index("ix_notification_channels_enabled", "channel_type", "enabled"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    channel_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="wecom_webhook"
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    webhook_url: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class ProfitAlertDelivery(Base):
    """止盈推送成功送达记录，用于按北京时间自然日与目标指纹幂等去重。"""

    __tablename__ = "profit_alert_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "lot_id",
            "target_key",
            "target_signature",
            "trigger_date",
            "channel_type",
            name="uq_profit_alert_deliveries_dedup",
        ),
        Index("ix_profit_alert_deliveries_user_date", "user_id", "trigger_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    lot_id: Mapped[str] = mapped_column(
        ForeignKey("lots.id", ondelete="CASCADE"), nullable=False
    )
    target_key: Mapped[str] = mapped_column(String(64), nullable=False)
    target_signature: Mapped[str] = mapped_column(String(128), nullable=False)
    trigger_date: Mapped[str] = mapped_column(String(10), nullable=False)
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False)
    triggered_price: Mapped[Decimal] = mapped_column(ExactDecimal(6), nullable=False)
    target_price: Mapped[Decimal] = mapped_column(ExactDecimal(6), nullable=False)
    is_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


__all__ = [
    "NotificationChannel",
    "ProfitAlertDelivery",
]
