from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect, text

from app.db.base import Base
from app.db.migrations import prepare_database_schema
from app.db.session import create_engine_for_url


def test_startup_schema_migrates_empty_database(tmp_path: Path) -> None:
    # 空库启动应走正式 Alembic 迁移并生成版本记录。
    database = tmp_path / 'fresh-startup.sqlite'
    engine = create_engine_for_url(f'sqlite:///{database.as_posix()}')
    try:
        prepare_database_schema(engine)
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            revision = connection.execute(
                text('SELECT version_num FROM alembic_version')
            ).scalar_one()
        assert {
            'accounts',
            'trades',
            'position_refresh_states',
            'users',
            'user_sessions',
            'notification_channels',
            'profit_alert_deliveries',
            'security_daily_metrics',
        } <= tables
        assert revision == '0007_security_daily_metrics'
    finally:
        engine.dispose()


def test_startup_schema_adopts_legacy_sqlite_without_data_loss(tmp_path: Path) -> None:
    # 旧版 create_all 库没有版本表，启动时应保留数据并幂等接管。
    database = tmp_path / 'legacy-startup.sqlite'
    engine = create_engine_for_url(f'sqlite:///{database.as_posix()}')
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(
                text(
                    'INSERT INTO accounts '
                    '(id, name, currency, created_at) '
                    'VALUES (:id, :name, :currency, :created_at)'
                ),
                {
                    'id': 'legacy-account',
                    'name': 'Legacy',
                    'currency': 'CNY',
                    'created_at': '2026-11-18 01:00:00',
                },
            )

        prepare_database_schema(engine)
        prepare_database_schema(engine)

        with engine.connect() as connection:
            count = connection.execute(text('SELECT count(*) FROM accounts')).scalar_one()
            revision = connection.execute(
                text('SELECT version_num FROM alembic_version')
            ).scalar_one()
            integrity = connection.execute(text('PRAGMA integrity_check')).scalar_one()
        assert count == 1
        assert revision == '0007_security_daily_metrics'
        assert integrity == 'ok'
    finally:
        engine.dispose()
