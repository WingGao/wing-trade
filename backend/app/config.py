"""应用配置：集中解析数据库、行情和服务运行参数。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy.engine import URL

SUPPORTED_DATABASE_BACKENDS = frozenset({"sqlite", "mysql"})
SUPPORTED_QUOTE_PROVIDERS = frozenset({"eastmoney", "mock"})
SUPPORTED_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


def normalize_log_level(value: str) -> str:
    """将日志等级规范为大写，并拒绝 Python 标准等级之外的值。"""
    normalized = value.strip().upper()
    if normalized not in SUPPORTED_LOG_LEVELS:
        allowed = ", ".join(sorted(SUPPORTED_LOG_LEVELS))
        raise ValueError(f"LOG_LEVEL must be one of: {allowed}")
    return normalized


def _required_env(name: str, *, trim: bool = True) -> str:
    """读取必填环境变量；按需清理首尾空白，并拒绝空值。"""
    raw_value = os.getenv(name)
    if raw_value is None or (not raw_value.strip() if trim else raw_value == ""):
        raise ValueError(f"{name} is required when DATABASE_BACKEND=mysql")
    return raw_value.strip() if trim else raw_value


def _mysql_database_url_from_env() -> str:
    """根据 MySQL 分项配置生成经过安全转义的 SQLAlchemy 连接地址。"""
    host = _required_env("MYSQL_HOST")
    database = _required_env("MYSQL_DATABASE")
    username = _required_env("MYSQL_USER")
    password = _required_env("MYSQL_PASSWORD", trim=False)
    port_text = _required_env("MYSQL_PORT")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError("MYSQL_PORT must be an integer between 1 and 65535") from exc
    if not 1 <= port <= 65535:
        raise ValueError("MYSQL_PORT must be an integer between 1 and 65535")

    return URL.create(
        drivername="mysql+pymysql",
        username=username,
        password=password,
        host=host,
        port=port,
        database=database,
        query={"charset": "utf8mb4"},
    ).render_as_string(hide_password=False)


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str = "sqlite:///./ledger.db"
    echo_sql: bool = False
    log_level: str = "INFO"
    quote_provider: str = "eastmoney"
    quote_timeout_seconds: Decimal = Decimal("5")

    def __post_init__(self) -> None:
        """校验直接构造或环境变量来源的日志与行情配置。"""
        log_level = normalize_log_level(self.log_level)
        provider = self.quote_provider.strip().lower()
        if provider not in SUPPORTED_QUOTE_PROVIDERS:
            allowed = ", ".join(sorted(SUPPORTED_QUOTE_PROVIDERS))
            raise ValueError(f"QUOTE_PROVIDER must be one of: {allowed}")
        timeout = self.quote_timeout_seconds
        if isinstance(timeout, float):
            timeout = Decimal(str(timeout))
        elif not isinstance(timeout, Decimal):
            try:
                timeout = Decimal(str(timeout))
            except InvalidOperation as exc:
                raise ValueError(
                    "QUOTE_TIMEOUT_SECONDS must be a finite positive number"
                ) from exc
        if not timeout.is_finite() or timeout <= 0:
            raise ValueError("QUOTE_TIMEOUT_SECONDS must be a finite positive number")
        object.__setattr__(self, "quote_provider", provider)
        object.__setattr__(self, "quote_timeout_seconds", timeout)
        object.__setattr__(self, "log_level", log_level)

    @classmethod
    def from_env(cls) -> Settings:
        """从环境变量读取并校验应用配置。"""
        defaults = cls()
        explicit_database_url = os.getenv("DATABASE_URL", "").strip()
        quote_provider = os.getenv("QUOTE_PROVIDER", defaults.quote_provider).strip().lower()
        if quote_provider not in SUPPORTED_QUOTE_PROVIDERS:
            allowed = ", ".join(sorted(SUPPORTED_QUOTE_PROVIDERS))
            raise ValueError(f"QUOTE_PROVIDER must be one of: {allowed}")
        timeout_raw = os.getenv(
            "QUOTE_TIMEOUT_SECONDS", str(defaults.quote_timeout_seconds)
        ).strip()
        try:
            quote_timeout_seconds = Decimal(timeout_raw)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("QUOTE_TIMEOUT_SECONDS must be a finite positive number") from exc
        if not quote_timeout_seconds.is_finite() or quote_timeout_seconds <= 0:
            raise ValueError("QUOTE_TIMEOUT_SECONDS must be a finite positive number")
        return cls(
            database_url=explicit_database_url or cls._database_url_from_env(defaults.database_url),
            echo_sql=os.getenv("ECHO_SQL", "0").lower() in {"1", "true", "yes"},
            log_level=os.getenv("LOG_LEVEL", defaults.log_level),
            quote_provider=quote_provider,
            quote_timeout_seconds=quote_timeout_seconds,
        )

    @staticmethod
    def _database_url_from_env(default_url: str) -> str:
        """按数据库后端解析连接地址，并保留 SQLite 的原默认行为。"""
        backend = os.getenv("DATABASE_BACKEND", "sqlite").strip().lower()
        if backend not in SUPPORTED_DATABASE_BACKENDS:
            allowed = ", ".join(sorted(SUPPORTED_DATABASE_BACKENDS))
            raise ValueError(f"DATABASE_BACKEND must be one of: {allowed}")
        if backend == "mysql":
            return _mysql_database_url_from_env()

        sqlite_path = os.getenv("DATABASE_SQLITE_PATH", "").strip()
        if not sqlite_path:
            return default_url
        return URL.create(drivername="sqlite", database=sqlite_path).render_as_string(
            hide_password=False
        )


settings = Settings.from_env()
