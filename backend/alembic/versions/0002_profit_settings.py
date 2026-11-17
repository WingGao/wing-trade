"""新增全局和个股止盈默认设置，不复制继承批次。"""

import sqlalchemy as sa

from alembic import op
from app.db.base import ExactDecimal

revision = "0002_profit_settings"
down_revision = "0001_initial_ledger_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """添加按作用域唯一的止盈配置表。"""
    op.create_table(
        "profit_settings",
        sa.Column("scope_key", sa.String(48), primary_key=True),
        sa.Column(
            "security_id",
            sa.String(36),
            sa.ForeignKey("securities.id", ondelete="CASCADE"),
        ),
        sa.Column("target_return_pct", ExactDecimal(6), nullable=False),
        sa.Column("calculation_basis", sa.String(8), nullable=False),
        sa.Column("alert_enabled", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("security_id", name="uq_profit_settings_security"),
    )


def downgrade() -> None:
    """删除本版本新增表，其余交易事实表保持不变。"""
    op.drop_table("profit_settings")
