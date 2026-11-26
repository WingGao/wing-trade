"""新增个股策略表档位表 security_strategy_rows。"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0009_security_strategy_rows"
down_revision = "0008_watchlist_buyback_alerts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """无损创建 security_strategy_rows 表及排序索引，兼容 SQLite 与 MySQL。"""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "security_strategy_rows" not in existing_tables:
        op.create_table(
            "security_strategy_rows",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "security_id",
                sa.String(36),
                sa.ForeignKey("securities.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("buy_price", sa.String(64), nullable=True),
            sa.Column("buy_qty", sa.String(64), nullable=True),
            sa.Column("sell_price", sa.String(64), nullable=True),
            sa.Column("sell_qty", sa.String(64), nullable=True),
            sa.Column("note", sa.String(256), nullable=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_security_strategy_rows_user_security_order",
            "security_strategy_rows",
            ["user_id", "security_id", "sort_order"],
        )


def downgrade() -> None:
    """回退删除 security_strategy_rows 表与索引。"""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "security_strategy_rows" in existing_tables:
        op.drop_index(
            "ix_security_strategy_rows_user_security_order",
            table_name="security_strategy_rows",
        )
        op.drop_table("security_strategy_rows")
