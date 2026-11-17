"""新增独立持仓范围的待刷新状态，不改写既有交易或派生账务。"""

import sqlalchemy as sa

from alembic import op

revision = "0003_position_refresh_states"
down_revision = "0002_profit_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """只创建新状态表和索引，既有范围默认无标记。"""
    op.create_table(
        "position_refresh_states",
        sa.Column("id", sa.String(36), primary_key=True),
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
            "strategy_id",
            sa.String(36),
            sa.ForeignKey("strategies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("needs_refresh", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "account_id", "security_id", "strategy_id", name="uq_position_refresh_states_scope"
        ),
    )
    op.create_index(
        "ix_position_refresh_states_account_pending",
        "position_refresh_states",
        ["account_id", "needs_refresh"],
    )


def downgrade() -> None:
    """仅移除本版本引入的状态表，不改动交易和审计。"""
    op.drop_index(
        "ix_position_refresh_states_account_pending", table_name="position_refresh_states"
    )
    op.drop_table("position_refresh_states")
