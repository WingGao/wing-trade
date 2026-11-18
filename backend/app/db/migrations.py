# 数据库结构初始化与版本迁移。

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import Engine, inspect

from alembic import command
from app.models.ledger import Base

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_KNOWN_SCHEMA_TABLES = frozenset(Base.metadata.tables)


def _alembic_config(connection: object) -> Config:
    # 创建绑定指定数据库连接的 Alembic 配置。
    config = Config(str(_BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    config.attributes["connection"] = connection
    return config


def _upgrade_to_head(database_engine: Engine) -> None:
    # 在独立连接上把数据库升级到当前迁移版本。
    with database_engine.connect() as connection:
        command.upgrade(_alembic_config(connection), "head")


def _stamp_head(database_engine: Engine) -> None:
    # 把旧版模型建表库标记为当前版本，不清空或重写账务数据。
    with database_engine.connect() as connection:
        command.stamp(_alembic_config(connection), "head")


def prepare_database_schema(database_engine: Engine) -> None:
    # 启动前准备结构，并兼容旧版无版本 SQLite 数据库。
    table_names = set(inspect(database_engine).get_table_names())
    has_version_table = "alembic_version" in table_names
    has_known_schema = bool(table_names & _KNOWN_SCHEMA_TABLES)

    if not has_version_table and has_known_schema:
        # 旧库先补齐当前模型表再接管版本，不删除或重写业务数据。
        Base.metadata.create_all(database_engine)
        _stamp_head(database_engine)
    else:
        # 空库执行正式迁移，已版本化数据库只升级尚未应用的 revision。
        _upgrade_to_head(database_engine)

    Base.metadata.create_all(database_engine)
