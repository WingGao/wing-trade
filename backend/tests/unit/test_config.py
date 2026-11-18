from __future__ import annotations

import pytest
from sqlalchemy.engine import make_url

from app.config import Settings
from app.db.session import create_engine_for_url

DATABASE_ENV_KEYS = (
    "DATABASE_URL",
    "DATABASE_BACKEND",
    "DATABASE_SQLITE_PATH",
    "MYSQL_HOST",
    "MYSQL_PORT",
    "MYSQL_DATABASE",
    "MYSQL_USER",
    "MYSQL_PASSWORD",
)


def _clear_database_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """清理数据库相关环境变量，确保测试不读取本机配置。"""
    for key in DATABASE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_mysql_settings_build_encoded_pymysql_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """MySQL 分项配置应生成带 utf8mb4 且可安全转义的连接地址。"""
    _clear_database_environment(monkeypatch)
    password = "p@ss:w/rd?#&%+"
    monkeypatch.setenv("DATABASE_BACKEND", "mysql")
    monkeypatch.setenv("MYSQL_HOST", "db.internal")
    monkeypatch.setenv("MYSQL_PORT", "3307")
    monkeypatch.setenv("MYSQL_DATABASE", "ashare_ledger")
    monkeypatch.setenv("MYSQL_USER", "ledger_user")
    monkeypatch.setenv("MYSQL_PASSWORD", password)

    settings = Settings.from_env()
    parsed = make_url(settings.database_url)

    assert settings.database_url.startswith("mysql+pymysql://")
    assert parsed.drivername == "mysql+pymysql"
    assert parsed.host == "db.internal"
    assert parsed.port == 3307
    assert parsed.database == "ashare_ledger"
    assert parsed.username == "ledger_user"
    assert parsed.password == password
    assert dict(parsed.query) == {"charset": "utf8mb4"}
    assert password not in settings.database_url
    assert "%40" in settings.database_url
    assert "%2F" in settings.database_url


def test_explicit_database_url_has_highest_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    """显式 DATABASE_URL 应优先于数据库后端和 MySQL 分项配置。"""
    _clear_database_environment(monkeypatch)
    monkeypatch.setenv("DATABASE_BACKEND", "mysql")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///explicit-ledger.db")

    settings = Settings.from_env()

    assert settings.database_url == "sqlite:///explicit-ledger.db"


def test_database_backend_defaults_to_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    """未指定数据库后端时应保留原有 SQLite 默认地址。"""
    _clear_database_environment(monkeypatch)

    settings = Settings.from_env()

    assert settings.database_url == "sqlite:///./ledger.db"


def test_sqlite_compose_path_keeps_data_mount_compatibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SQLite 后端应支持 Compose 指定的持久化文件路径。"""
    _clear_database_environment(monkeypatch)
    monkeypatch.setenv("DATABASE_BACKEND", "sqlite")
    monkeypatch.setenv("DATABASE_SQLITE_PATH", "/app/data/ashare_journal.db")

    settings = Settings.from_env()

    assert settings.database_url == "sqlite:////app/data/ashare_journal.db"


def test_invalid_database_backend_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """非法数据库后端应在启动配置阶段直接拒绝。"""
    _clear_database_environment(monkeypatch)
    monkeypatch.setenv("DATABASE_BACKEND", "postgresql")

    with pytest.raises(ValueError, match="DATABASE_BACKEND"):
        Settings.from_env()


def test_mysql_engine_enables_pre_ping_and_connection_recycling() -> None:
    """MySQL 引擎应启用连接探活和连接回收，且创建时不建立网络连接。"""
    engine = create_engine_for_url(
        "mysql+pymysql://user:password@localhost:3306/ashare_ledger?charset=utf8mb4",
        echo=False,
    )

    try:
        assert engine.pool._pre_ping is True
        assert engine.pool._recycle == 1800
        assert engine.dialect.driver == "pymysql"
    finally:
        engine.dispose()


def test_empty_explicit_database_url_uses_mysql_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Compose 传入空 DATABASE_URL 时不应覆盖 MySQL 分项配置。
    _clear_database_environment(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("DATABASE_BACKEND", "mysql")
    monkeypatch.setenv("MYSQL_HOST", "mysql")
    monkeypatch.setenv("MYSQL_PORT", "3306")
    monkeypatch.setenv("MYSQL_DATABASE", "ashare_ledger")
    monkeypatch.setenv("MYSQL_USER", "ashare")
    monkeypatch.setenv("MYSQL_PASSWORD", "secret")

    settings = Settings.from_env()

    assert settings.database_url.startswith("mysql+pymysql://")
    assert "@mysql:3306/ashare_ledger" in settings.database_url
