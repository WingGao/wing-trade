"""为 securities 表新增场外基金盘中估算参考标的与跟踪仓位系数列。"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0005_security_proxy_fields"
down_revision = "0004_user_auth_system"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """无损为 securities 表增量添加 proxy_symbol、proxy_name 和 tracking_ratio 列。"""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("securities")}

    if "proxy_symbol" not in existing_columns:
        op.add_column("securities", sa.Column("proxy_symbol", sa.String(32), nullable=True))
    if "proxy_name" not in existing_columns:
        op.add_column("securities", sa.Column("proxy_name", sa.String(128), nullable=True))
    if "tracking_ratio" not in existing_columns:
        op.add_column(
            "securities",
            sa.Column(
                "tracking_ratio",
                sa.Numeric(20, 6),
                nullable=False,
                server_default="0.950000",
            ),
        )


def downgrade() -> None:
    """回退移除 securities 表上的参考标的估算字段。"""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("securities")}

    if "tracking_ratio" in existing_columns:
        op.drop_column("securities", "tracking_ratio")
    if "proxy_name" in existing_columns:
        op.drop_column("securities", "proxy_name")
    if "proxy_symbol" in existing_columns:
        op.drop_column("securities", "proxy_symbol")
