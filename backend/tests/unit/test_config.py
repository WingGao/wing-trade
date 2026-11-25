"""应用配置解析单元测试：覆盖 YAML 优先、环境变量回退与异常校验。"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

import app.config as config_module
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

ALL_CONFIG_ENV_KEYS = (
    *DATABASE_ENV_KEYS,
    "CONFIG_FILE",
    "SERVER_PORT",
    "ECHO_SQL",
    "LOG_LEVEL",
    "QUOTE_PROVIDER",
    "QUOTE_TIMEOUT_SECONDS",
    "LONGBRIDGE_APP_KEY",
    "LONGBRIDGE_APP_SECRET",
    "LONGBRIDGE_ACCESS_TOKEN",
    "LONGBRIDGE_ENABLE_OVERNIGHT",
    "LONGBRIDGE_HTTP_URL",
    "LONGBRIDGE_QUOTE_WS_URL",
    "DEFAULT_ADMIN_EMAIL",
    "AUTH_SECRET_KEY",
    "AUTH_SESSION_DAYS",
    "AUTH_COOKIE_NAME",
    "AUTH_COOKIE_SECURE",
    "EMAIL_PROVIDER",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "SMTP_FROM",
    "SMTP_USE_TLS",
    "SMTP_USE_SSL",
    "FRONTEND_DIST_DIR",
)


def _clear_database_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """清理数据库相关环境变量，确保测试不读取本机配置。"""
    for key in DATABASE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _clear_all_config_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """清理全部应用配置环境变量，确保测试环境完全受控。"""
    for key in ALL_CONFIG_ENV_KEYS:
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

    assert settings.server_port == 12011
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
    """Compose 传入空 DATABASE_URL 时不应覆盖 MySQL 分项配置。"""
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


def test_structured_yaml_overrides_env_including_falsy_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """结构化 YAML 应优先覆盖同名环境变量，包括 false 等假值配置。"""
    _clear_all_config_environment(monkeypatch)
    monkeypatch.setenv("SERVER_PORT", "8081")
    monkeypatch.setenv("DATABASE_BACKEND", "sqlite")
    monkeypatch.setenv("ECHO_SQL", "true")
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    monkeypatch.setenv("QUOTE_PROVIDER", "eastmoney")
    monkeypatch.setenv("QUOTE_TIMEOUT_SECONDS", "9")
    monkeypatch.setenv("DEFAULT_ADMIN_EMAIL", "env@example.com")
    monkeypatch.setenv("AUTH_SECRET_KEY", "env-secret")
    monkeypatch.setenv("AUTH_SESSION_DAYS", "10")
    monkeypatch.setenv("AUTH_COOKIE_NAME", "env_cookie")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    monkeypatch.setenv("EMAIL_PROVIDER", "console")
    monkeypatch.setenv("FRONTEND_DIST_DIR", "/env/static")

    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(
        """
server:
  port: 9001
database:
  backend: mysql
  echo_sql: false
  mysql:
    host: yaml-db.internal
    port: 3307
    database: yaml_ledger
    user: yaml_user
    password: " p@ss/word "
logging:
  level: debug
quote:
  provider: mock
  timeout_seconds: 2.5
auth:
  default_admin_email: yaml-admin@example.com
  secret_key: yaml-secret
  session_days: 14
  cookie_name: yaml_session
  cookie_secure: false
email:
  provider: smtp
  smtp:
    host: smtp.yaml.local
    port: 465
    user: mailer@yaml.local
    password: " smtp secret "
    from: noreply@yaml.local
frontend:
  dist_dir: /yaml/static
""".strip(),
        encoding="utf-8",
    )

    settings = Settings.load(yaml_file)
    parsed_db = make_url(settings.database_url)

    assert parsed_db.drivername == "mysql+pymysql"
    assert parsed_db.host == "yaml-db.internal"
    assert parsed_db.port == 3307
    assert parsed_db.database == "yaml_ledger"
    assert parsed_db.username == "yaml_user"
    assert parsed_db.password == " p@ss/word "
    assert settings.server_port == 9001
    assert settings.echo_sql is False
    assert settings.log_level == "DEBUG"
    assert settings.quote_provider == "mock"
    assert settings.quote_timeout_seconds == Decimal("2.5")
    assert settings.default_admin_email == "yaml-admin@example.com"
    assert settings.auth_secret_key == "yaml-secret"
    assert settings.auth_session_days == 14
    assert settings.auth_cookie_name == "yaml_session"
    assert settings.auth_cookie_secure is False
    assert settings.email_provider == "smtp"
    assert settings.smtp_host == "smtp.yaml.local"
    assert settings.smtp_port == 465
    assert settings.smtp_user == "mailer@yaml.local"
    assert settings.smtp_password == " smtp secret "
    assert settings.smtp_from == "noreply@yaml.local"
    assert settings.smtp_use_ssl is True
    assert settings.smtp_use_tls is False
    assert settings.frontend_dist_dir == "/yaml/static"


def test_partial_yaml_and_null_fields_fallback_to_env_and_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """YAML 中省略或设为 null 的字段应按字段粒度回退到环境变量与默认值。"""
    _clear_all_config_environment(monkeypatch)
    monkeypatch.setenv("SERVER_PORT", "8100")
    monkeypatch.setenv("DATABASE_BACKEND", "mysql")
    monkeypatch.setenv("MYSQL_HOST", "env-mysql")
    monkeypatch.setenv("MYSQL_PORT", "3306")
    monkeypatch.setenv("MYSQL_DATABASE", "env_db")
    monkeypatch.setenv("MYSQL_USER", "env_user")
    monkeypatch.setenv("MYSQL_PASSWORD", "env_pwd")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("QUOTE_TIMEOUT_SECONDS", "8")
    monkeypatch.setenv("AUTH_COOKIE_NAME", "env_session")

    yaml_file = tmp_path / "partial.yaml"
    yaml_file.write_text(
        """
server:
  port: null
database:
  url: ""
  mysql:
    host: yaml-mysql
    password: null
logging:
  level: null
quote:
  provider: mock
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_FILE", str(yaml_file))

    settings = Settings.from_env()
    parsed_db = make_url(settings.database_url)

    assert parsed_db.host == "yaml-mysql"
    assert parsed_db.port == 3306
    assert parsed_db.database == "env_db"
    assert parsed_db.username == "env_user"
    assert parsed_db.password == "env_pwd"
    assert settings.server_port == 8100
    assert settings.log_level == "WARNING"
    assert settings.quote_provider == "mock"
    assert settings.quote_timeout_seconds == Decimal("8")
    assert settings.auth_cookie_name == "env_session"
    assert settings.auth_session_days == 30


def test_default_yaml_discovery_prefers_config_yaml_over_yml(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """未指定 CONFIG_FILE 时应自动探测默认目录中的 config.yaml，其次 config.yml。"""
    _clear_all_config_environment(monkeypatch)
    yml_file = tmp_path / "config.yml"
    yml_file.write_text("logging:\n  level: WARNING\n", encoding="utf-8")
    monkeypatch.setattr(
        config_module,
        "_default_config_candidates",
        lambda: (tmp_path / "config.yaml", yml_file),
    )

    assert Settings.load().log_level == "WARNING"

    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text("logging:\n  level: ERROR\n", encoding="utf-8")

    assert Settings.load().log_level == "ERROR"


def test_empty_yaml_file_falls_back_to_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """空或全注释的 YAML 文件应视为空配置并正常回退环境变量。"""
    _clear_all_config_environment(monkeypatch)
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    empty_file = tmp_path / "empty.yaml"
    empty_file.write_text("# only comments\n", encoding="utf-8")

    settings = Settings.load(empty_file)

    assert settings.log_level == "WARNING"
    assert settings.server_port == 12011
    assert settings.database_url == "sqlite:///./ledger.db"


def test_yaml_error_cases_raise_clear_value_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """缺失显式文件、语法错误、非映射根节点、未知键与布尔误传整数均应报错。"""
    _clear_all_config_environment(monkeypatch)

    missing = tmp_path / "missing.yaml"
    monkeypatch.setenv("CONFIG_FILE", str(missing))
    with pytest.raises(ValueError, match="CONFIG_FILE not found"):
        Settings.from_env()
    monkeypatch.delenv("CONFIG_FILE", raising=False)

    bad_syntax = tmp_path / "bad.yaml"
    bad_syntax.write_text("database:\n  backend: [unclosed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid YAML syntax"):
        Settings.load(bad_syntax)

    non_mapping = tmp_path / "list.yaml"
    non_mapping.write_text("- item1\n- item2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML mapping"):
        Settings.load(non_mapping)

    unknown_key = tmp_path / "unknown.yaml"
    unknown_key.write_text("quote:\n  timeout: 5\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown config key: quote.timeout"):
        Settings.load(unknown_key)

    bool_port = tmp_path / "bool_port.yaml"
    bool_port.write_text("email:\n  smtp:\n    port: true\n", encoding="utf-8")
    with pytest.raises(ValueError, match="SMTP_PORT"):
        Settings.load(bool_port)

    bool_days = tmp_path / "bool_days.yaml"
    bool_days.write_text("auth:\n  session_days: true\n", encoding="utf-8")
    with pytest.raises(ValueError, match="AUTH_SESSION_DAYS"):
        Settings.load(bool_days)

    bool_server_port = tmp_path / "bool_server_port.yaml"
    bool_server_port.write_text("server:\n  port: true\n", encoding="utf-8")
    with pytest.raises(ValueError, match="SERVER_PORT"):
        Settings.load(bool_server_port)


def test_server_port_defaults_env_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """后端端口应默认 12011、支持 SERVER_PORT 环境变量并拦截非法端口值。"""
    _clear_all_config_environment(monkeypatch)

    assert Settings.load(discover_default=False).server_port == 12011

    monkeypatch.setenv("SERVER_PORT", "8088")
    assert Settings.load(discover_default=False).server_port == 8088

    for invalid_port in ("0", "65536", "abc"):
        monkeypatch.setenv("SERVER_PORT", invalid_port)
        with pytest.raises(ValueError, match="SERVER_PORT"):
            Settings.load(discover_default=False)


def test_repo_config_example_yaml_is_loadable(monkeypatch: pytest.MonkeyPatch) -> None:
    """仓库根目录的 config.example.yaml 模板应能直接通过配置解析与校验。"""
    _clear_all_config_environment(monkeypatch)
    example_path = Path(__file__).resolve().parents[3] / "config.example.yaml"

    settings = Settings.load(example_path)

    assert settings.server_port == 12011
    assert settings.database_url == "sqlite:///./ledger.db"
    assert settings.log_level == "INFO"
    assert settings.quote_provider == "eastmoney"
    assert settings.email_provider == "console"


def test_longbridge_config_yaml_overrides_env_and_requires_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """长桥模式应支持 YAML > env 优先级、布尔假值覆盖，并在缺失三元组时报错。"""
    _clear_all_config_environment(monkeypatch)
    monkeypatch.setenv("QUOTE_PROVIDER", "longbridge")
    monkeypatch.setenv("LONGBRIDGE_APP_KEY", "env-key")
    monkeypatch.setenv("LONGBRIDGE_APP_SECRET", "env-secret")
    monkeypatch.setenv("LONGBRIDGE_ACCESS_TOKEN", "env-token")
    monkeypatch.setenv("LONGBRIDGE_ENABLE_OVERNIGHT", "true")
    monkeypatch.setenv("LONGBRIDGE_HTTP_URL", "https://env.longbridge.cn")

    yaml_file = tmp_path / "longbridge.yaml"
    yaml_file.write_text(
        """
quote:
  provider: longbridge
  longbridge:
    app_key: yaml-key
    app_secret: ""
    access_token: yaml-token
    enable_overnight: false
    quote_ws_url: wss://openapi-quote.longbridge.cn
""".strip(),
        encoding="utf-8",
    )

    loaded = Settings.load(yaml_file)
    assert loaded.quote_provider == "longbridge"
    assert loaded.longbridge_app_key == "yaml-key"
    assert loaded.longbridge_app_secret == "env-secret"
    assert loaded.longbridge_access_token == "yaml-token"
    assert loaded.longbridge_enable_overnight is False
    assert loaded.longbridge_http_url == "https://env.longbridge.cn"
    assert loaded.longbridge_quote_ws_url == "wss://openapi-quote.longbridge.cn"

    _clear_all_config_environment(monkeypatch)
    monkeypatch.setenv("QUOTE_PROVIDER", "longbridge")
    monkeypatch.setenv("LONGBRIDGE_APP_KEY", "only-key")
    monkeypatch.setenv("LONGBRIDGE_APP_SECRET", "only-secret")
    with pytest.raises(ValueError, match="LONGBRIDGE_ACCESS_TOKEN"):
        Settings.load(discover_default=False)
