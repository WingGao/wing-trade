"""只在临时 SQLite 上验证新增设置迁移，不使用用户本地账本。"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_profit_setting_migration_keeps_existing_security_and_reverses(tmp_path: Path) -> None:
    """从旧版本升级保留既有证券，新增表可回退，最后可降至空版本。"""
    database = tmp_path / "profit-setting-migration.sqlite"
    backend = Path(__file__).resolve().parents[2]
    environment = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{database.as_posix()}",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }

    def migrate(target: str, *, downgrade: bool = False) -> None:
        """显式指定临时库 URL 后执行迁移，禁止使用默认 ledger.db。"""
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade" if downgrade else "upgrade", target],
            cwd=backend,
            env=environment,
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    migrate("0001_initial_ledger_schema")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO securities (id, market, symbol, name, security_type, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("existing-security", "SH", "600519", "贵州茅台", "STOCK", "ACTIVE", "2026-11-17"),
        )
    migrate("head")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT name FROM securities").fetchall() == [("贵州茅台",)]
        assert connection.execute("SELECT count(*) FROM profit_settings").fetchone() == (0,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    migrate("0001_initial_ledger_schema", downgrade=True)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT name FROM securities").fetchall() == [("贵州茅台",)]
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='profit_settings'"
            ).fetchall()
            == []
        )
    migrate("base", downgrade=True)
