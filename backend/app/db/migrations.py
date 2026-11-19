"""数据库结构初始化与版本迁移。"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import Engine, inspect

from alembic import command
from app.models import Base

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_KNOWN_SCHEMA_TABLES = frozenset(Base.metadata.tables)
_PRE_AUTH_REVISION = "0003_position_refresh_states"


def _alembic_config(connection: object) -> Config:
    """创建绑定指定数据库连接的 Alembic 配置。"""
    config = Config(str(_BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    config.attributes["connection"] = connection
    return config


def _upgrade_to_head(database_engine: Engine) -> None:
    """在独立连接上把数据库升级到当前迁移版本。"""
    with database_engine.connect() as connection:
        command.upgrade(_alembic_config(connection), "head")


def _stamp_revision(database_engine: Engine, revision: str) -> None:
    """将已有库标记为指定迁移版本，不清空或重写账务数据。"""
    with database_engine.connect() as connection:
        command.stamp(_alembic_config(connection), revision)


def prepare_database_schema(database_engine: Engine) -> None:
    """启动前准备结构，并无损兼容旧版无版本 SQLite 数据库。"""
    inspector = inspect(database_engine)
    table_names = set(inspector.get_table_names())
    has_version_table = "alembic_version" in table_names
    has_known_schema = bool(table_names & _KNOWN_SCHEMA_TABLES)

    if not has_version_table and has_known_schema:
        account_columns = (
            {col["name"] for col in inspector.get_columns("accounts")}
            if "accounts" in table_names
            else set()
        )
        if "accounts" in table_names and "user_id" not in account_columns:
            # 旧版无版本账务库：先补齐 0003 及之前可能缺失的辅助表，再通过 0004 迁移无损加列。
            pre_auth_tables = [
                table
                for name, table in Base.metadata.tables.items()
                if name not in {"users", "email_verification_codes", "user_sessions"}
                and name not in table_names
            ]
            if pre_auth_tables:
                Base.metadata.create_all(database_engine, tables=pre_auth_tables)
            _stamp_revision(database_engine, _PRE_AUTH_REVISION)
            _upgrade_to_head(database_engine)
        else:
            # 已经具备完整模型列的无版本库：补齐缺失表后直接标记 head。
            Base.metadata.create_all(database_engine)
            _stamp_revision(database_engine, "head")
    else:
        # 空库执行正式迁移，已版本化数据库只升级尚未应用的 revision。
        _upgrade_to_head(database_engine)

    Base.metadata.create_all(database_engine)
