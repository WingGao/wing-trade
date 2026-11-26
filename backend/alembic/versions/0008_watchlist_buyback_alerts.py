"""扩展 watchlist_items 目标买回价字段并新增 buyback_alert_deliveries 去重表。"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0008_watchlist_buyback_alerts"
down_revision = "0007_security_daily_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """无损扩展关注表字段并创建买回提醒去重表，兼容 SQLite 与 MySQL。"""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "watchlist_items" in existing_tables:
        existing_cols = {col["name"] for col in inspector.get_columns("watchlist_items")}
        if "target_buy_price" not in existing_cols:
            op.add_column(
                "watchlist_items",
                sa.Column("target_buy_price", sa.String(64), nullable=True),
            )
        if "alert_enabled" not in existing_cols:
            op.add_column(
                "watchlist_items",
                sa.Column(
                    "alert_enabled",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                ),
            )
        if "note" not in existing_cols:
            op.add_column(
                "watchlist_items",
                sa.Column("note", sa.String(256), nullable=True),
            )
        if "updated_at" not in existing_cols:
            op.add_column(
                "watchlist_items",
                sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            )
            op.execute(
                sa.text(
                    "UPDATE watchlist_items SET updated_at = created_at WHERE updated_at IS NULL"
                )
            )

    if "buyback_alert_deliveries" not in existing_tables:
        op.create_table(
            "buyback_alert_deliveries",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "account_id",
                sa.String(36),
                sa.ForeignKey("accounts.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "security_id",
                sa.String(36),
                sa.ForeignKey("securities.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "watchlist_item_id",
                sa.String(36),
                sa.ForeignKey("watchlist_items.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("target_signature", sa.String(128), nullable=False),
            sa.Column("trigger_date", sa.String(10), nullable=False),
            sa.Column("channel_type", sa.String(32), nullable=False),
            sa.Column("triggered_price", sa.String(64), nullable=False),
            sa.Column("target_price", sa.String(64), nullable=False),
            sa.Column(
                "is_estimated",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "user_id",
                "watchlist_item_id",
                "target_signature",
                "trigger_date",
                "channel_type",
                name="uq_buyback_alert_deliveries_dedup",
            ),
        )
        op.create_index(
            "ix_buyback_alert_deliveries_user_date",
            "buyback_alert_deliveries",
            ["user_id", "trigger_date"],
        )


def downgrade() -> None:
    """回退删除买回提醒去重表及 watchlist_items 新增列。"""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "buyback_alert_deliveries" in existing_tables:
        op.drop_index(
            "ix_buyback_alert_deliveries_user_date",
            table_name="buyback_alert_deliveries",
        )
        op.drop_table("buyback_alert_deliveries")

    if "watchlist_items" in existing_tables:
        existing_cols = {col["name"] for col in inspector.get_columns("watchlist_items")}
        for col_name in ("updated_at", "note", "alert_enabled", "target_buy_price"):
            if col_name in existing_cols:
                op.drop_column("watchlist_items", col_name)
