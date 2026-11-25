"""新增证券日频指标缓存表（近 20 个交易日平均振幅）。"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0007_security_daily_metrics"
down_revision = "0006_take_profit_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """无损创建 security_daily_metrics 缓存表，不影响任何既有账务表。"""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "security_daily_metrics" not in existing_tables:
        op.create_table(
            "security_daily_metrics",
            sa.Column(
                "security_id",
                sa.String(36),
                sa.ForeignKey("securities.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("avg_amplitude_20d", sa.String(64), nullable=True),
            sa.Column("amplitude_sample_days", sa.Integer(), nullable=True),
            sa.Column("amplitude_as_of_date", sa.String(10), nullable=True),
            sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("provider", sa.String(64), nullable=True),
        )


def downgrade() -> None:
    """回退删除 security_daily_metrics 缓存表。"""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "security_daily_metrics" in existing_tables:
        op.drop_table("security_daily_metrics")
