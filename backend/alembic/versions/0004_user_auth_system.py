"""引入用户体系、邮箱验证码、服务端会话及账户/止盈设置的用户维度隔离。"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0004_user_auth_system"
down_revision = "0003_position_refresh_states"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建认证相关表，并无损为 accounts 和 profit_settings 补充用户归属列。"""
    bind = op.get_bind()
    dialect = bind.dialect.name
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "users" not in existing_tables:
        op.create_table(
            "users",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("email", sa.String(255), nullable=False, unique=True),
            sa.Column("display_name", sa.String(128), nullable=True),
            sa.Column("role", sa.String(16), nullable=False, server_default="USER"),
            sa.Column("status", sa.String(16), nullable=False, server_default="INVITED"),
            sa.Column(
                "is_default_admin",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column(
                "invited_by_user_id",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_users_role_status", "users", ["role", "status"])

    if "email_verification_codes" not in existing_tables:
        op.create_table(
            "email_verification_codes",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("email", sa.String(255), nullable=False),
            sa.Column("code_hash", sa.String(128), nullable=False),
            sa.Column("purpose", sa.String(32), nullable=False, server_default="LOGIN"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_email_codes_email_created",
            "email_verification_codes",
            ["email", "created_at"],
        )

    if "user_sessions" not in existing_tables:
        op.create_table(
            "user_sessions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("user_agent", sa.String(255), nullable=True),
            sa.Column("ip_address", sa.String(64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])

    # 为 accounts 无损追加 user_id，避免在 SQLite 重建表触发子表级联删除。
    account_columns = {col["name"] for col in inspector.get_columns("accounts")}
    if "user_id" not in account_columns:
        op.add_column("accounts", sa.Column("user_id", sa.String(36), nullable=True))
        if dialect != "sqlite":
            op.create_foreign_key(
                "fk_accounts_user_id_users",
                "accounts",
                "users",
                ["user_id"],
                ["id"],
                ondelete="CASCADE",
            )
    account_indexes = {idx["name"] for idx in inspector.get_indexes("accounts")}
    if "ix_accounts_user_id" not in account_indexes:
        op.create_index("ix_accounts_user_id", "accounts", ["user_id"])

    # 升级 profit_settings 为按用户隔离（无其他表引用该表）。
    profit_columns = {col["name"] for col in inspector.get_columns("profit_settings")}
    if "user_id" not in profit_columns:
        if dialect == "sqlite":
            with op.batch_alter_table("profit_settings") as batch_op:
                batch_op.drop_constraint("uq_profit_settings_security", type_="unique")
                batch_op.alter_column(
                    "scope_key",
                    existing_type=sa.String(48),
                    type_=sa.String(96),
                    existing_nullable=False,
                )
                batch_op.add_column(sa.Column("user_id", sa.String(36), nullable=True))
                batch_op.create_foreign_key(
                    "fk_profit_settings_user_id_users",
                    "users",
                    ["user_id"],
                    ["id"],
                    ondelete="CASCADE",
                )
                batch_op.create_unique_constraint(
                    "uq_profit_settings_user_security",
                    ["user_id", "security_id"],
                )
                batch_op.create_index("ix_profit_settings_user_id", ["user_id"])
        else:
            op.create_index(
                "ix_profit_settings_security_id", "profit_settings", ["security_id"]
            )
            op.drop_constraint(
                "uq_profit_settings_security", "profit_settings", type_="unique"
            )
            op.alter_column(
                "profit_settings",
                "scope_key",
                existing_type=sa.String(48),
                type_=sa.String(96),
                existing_nullable=False,
            )
            op.add_column("profit_settings", sa.Column("user_id", sa.String(36), nullable=True))
            op.create_foreign_key(
                "fk_profit_settings_user_id_users",
                "profit_settings",
                "users",
                ["user_id"],
                ["id"],
                ondelete="CASCADE",
            )
            op.create_unique_constraint(
                "uq_profit_settings_user_security",
                "profit_settings",
                ["user_id", "security_id"],
            )
            op.create_index("ix_profit_settings_user_id", "profit_settings", ["user_id"])


def downgrade() -> None:
    """回滚用户体系表与关联列，保留既有账本记录结构。"""
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "sqlite":
        with op.batch_alter_table("profit_settings") as batch_op:
            batch_op.drop_index("ix_profit_settings_user_id")
            batch_op.drop_constraint("uq_profit_settings_user_security", type_="unique")
            batch_op.drop_constraint("fk_profit_settings_user_id_users", type_="foreignkey")
            batch_op.drop_column("user_id")
            batch_op.alter_column(
                "scope_key",
                existing_type=sa.String(96),
                type_=sa.String(48),
                existing_nullable=False,
            )
            batch_op.create_unique_constraint("uq_profit_settings_security", ["security_id"])
    else:
        op.drop_index("ix_profit_settings_user_id", table_name="profit_settings")
        op.drop_constraint(
            "uq_profit_settings_user_security", "profit_settings", type_="unique"
        )
        op.drop_constraint(
            "fk_profit_settings_user_id_users", "profit_settings", type_="foreignkey"
        )
        op.drop_column("profit_settings", "user_id")
        op.alter_column(
            "profit_settings",
            "scope_key",
            existing_type=sa.String(96),
            type_=sa.String(48),
            existing_nullable=False,
        )
        op.create_unique_constraint(
            "uq_profit_settings_security", "profit_settings", ["security_id"]
        )
        op.drop_index("ix_profit_settings_security_id", table_name="profit_settings")

    op.drop_index("ix_accounts_user_id", table_name="accounts")
    if dialect != "sqlite":
        op.drop_constraint("fk_accounts_user_id_users", "accounts", type_="foreignkey")
    op.drop_column("accounts", "user_id")

    op.drop_index("ix_user_sessions_user_id", table_name="user_sessions")
    op.drop_table("user_sessions")

    op.drop_index("ix_email_codes_email_created", table_name="email_verification_codes")
    op.drop_table("email_verification_codes")

    op.drop_index("ix_users_role_status", table_name="users")
    op.drop_table("users")
