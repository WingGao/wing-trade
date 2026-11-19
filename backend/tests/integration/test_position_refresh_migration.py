"""仅在临时文件账本上验证待刷新迁移，保留既有事实与审计。"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.db.session import create_engine_for_url
from app.models import Account, Security, Strategy
from app.services.ledger import TradeLedgerService


def test_refresh_migration_preserves_all_old_rows_and_reverses(tmp_path: Path) -> None:
    """0002 升级不改写流水和审计，唯一约束生效，降级只移除新状态表。"""
    database = tmp_path / "refresh-migration.sqlite"
    backend = Path(__file__).resolve().parents[2]
    environment = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{database.as_posix()}",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }

    def migrate(target: str, *, downgrade: bool = False) -> None:
        """子进程显式使用临时 URL，防止命令落到默认 ledger.db。"""
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade" if downgrade else "upgrade", target],
            cwd=backend,
            env=environment,
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def original_rows() -> dict[str, list[tuple]]:
        """按列比较所有旧业务表，排除版本表及本次新增状态表。"""
        with sqlite3.connect(database) as connection:
            tables = [
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
                if row[0] not in {"alembic_version", "position_refresh_states"}
            ]
            return {
                table: sorted(connection.execute(f'SELECT * FROM "{table}"').fetchall())
                for table in tables
            }

    migrate("0002_profit_settings")
    engine = create_engine_for_url(environment["DATABASE_URL"])
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO accounts (id, name, broker, currency, created_at) VALUES (?, ?, ?, ?, ?)",
            ("acc-old", "旧账户", None, "CNY", "2026-11-01T00:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO securities (id, market, symbol, name, security_type, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("sec-old", "SH", "600519", "贵州茅台", "STOCK", "ACTIVE", "2026-11-01T00:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO strategies (id, account_id, name, created_at) VALUES (?, ?, ?, ?)",
            ("str-old", "acc-old", "DEFAULT", "2026-11-01T00:00:00+00:00"),
        )
        connection.commit()
    with Session(engine, expire_on_commit=False) as session:
        service = TradeLedgerService(session)
        service.buy(
            account_id="acc-old",
            security_id="sec-old",
            strategy_id="str-old",
            executed_at=datetime(2026, 11, 1, tzinfo=UTC),
            qty="10",
            price="10.123456",
            memo="迁移前原始成交",
            client_request_id="migration-existing",
        )
        service.sell(
            account_id="acc-old",
            security_id="sec-old",
            strategy_id="str-old",
            executed_at=datetime(2026, 11, 2, tzinfo=UTC),
            qty="3",
            price="11",
        )
        scope = "acc-old", "sec-old", "str-old"
    engine.dispose()
    before = original_rows()
    migrate("0003_position_refresh_states")
    assert original_rows() == before
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        assert connection.execute("SELECT count(*) FROM position_refresh_states").fetchone() == (0,)
        sql = (
            "INSERT INTO position_refresh_states "
            "(id, account_id, security_id, strategy_id, needs_refresh, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)"
        )
        connection.execute(sql, ("state-1", *scope, True, "2026-11-18T01:00:00"))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(sql, ("state-2", *scope, True, "2026-11-18T01:00:00"))
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    migrate("head")
    migrate("0002_profit_settings", downgrade=True)
    assert original_rows() == before
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name='position_refresh_states'"
            ).fetchall()
            == []
        )
    migrate("base", downgrade=True)
