"""新增用户通知渠道配置表与止盈推送自然日去重记录表。"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0006_take_profit_notifications"
down_revision = "0005_security_proxy_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """无损创建 notification_channels 与 profit_alert_deliveries 两张新表。"""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "notification_channels" not in existing_tables:
        op.create_table(
            "notification_channels",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "channel_type",
                sa.String(32),
                nullable=False,
                server_default="wecom_webhook",
            ),
            sa.Column(
                "enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column("webhook_url", sa.String(512), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "user_id",
                "channel_type",
                name="uq_notification_channels_user_type",
            ),
        )
        op.create_index(
            "ix_notification_channels_enabled",
            "notification_channels",
            ["channel_type", "enabled"],
        )

    if "profit_alert_deliveries" not in existing_tables:
        op.create_table(
            "profit_alert_deliveries",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "lot_id",
                sa.String(36),
                sa.ForeignKey("lots.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("target_key", sa.String(64), nullable=False),
            sa.Column("target_signature", sa.String(128), nullable=False),
            sa.Column("trigger_date", sa.String(10), nullable=False),
            sa.Column("channel_type", sa.String(32), nullable=False),
            sa.Column("triggered_price", sa.String(32), nullable=False),
            sa.Column("target_price", sa.String(32), nullable=False),
            sa.Column(
                "is_estimated",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "user_id",
                "lot_id",
                "target_key",
                "target_signature",
                "trigger_date",
                "channel_type",
                name="uq_profit_alert_deliveries_dedup",
            ),
        )
        op.create_index(
            "ix_profit_alert_deliveries_user_date",
            "profit_alert_deliveries",
            ["user_id", "trigger_date"],
        )


def downgrade() -> None:
    """回退删除止盈推送新增的两张独立表，不影响任何账务表。"""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "profit_alert_deliveries" in existing_tables:
        op.drop_index("ix_profit_alert_deliveries_user_date", table_name="profit_alert_deliveries")
        op.drop_table("profit_alert_deliveries")

    if "notification_channels" in existing_tables:
        op.drop_index("ix_notification_channels_enabled", table_name="notification_channels")
        op.drop_table("notification_channels")
