"""应用配置：集中解析数据库、行情、认证与邮件发送参数。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy.engine import URL

SUPPORTED_DATABASE_BACKENDS = frozenset({"sqlite", "mysql"})
SUPPORTED_QUOTE_PROVIDERS = frozenset({"eastmoney", "mock"})
SUPPORTED_EMAIL_PROVIDERS = frozenset({"console", "smtp"})
SUPPORTED_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
DEFAULT_DEV_ADMIN_EMAIL = "admin@localhost"
DEFAULT_DEV_AUTH_SECRET = "wing-trade-dev-auth-secret-change-me"


def normalize_log_level(value: str) -> str:
    """将日志等级规范为大写，并拒绝 Python 标准等级之外的值。"""
    normalized = value.strip().upper()
    if normalized not in SUPPORTED_LOG_LEVELS:
        allowed = ", ".join(sorted(SUPPORTED_LOG_LEVELS))
        raise ValueError(f"LOG_LEVEL must be one of: {allowed}")
    return normalized


def _required_env(name: str, *, trim: bool = True, context: str = "DATABASE_BACKEND=mysql") -> str:
    """读取必填环境变量；按需清理首尾空白，并拒绝空值。"""
    raw_value = os.getenv(name)
    if raw_value is None or (not raw_value.strip() if trim else raw_value == ""):
        raise ValueError(f"{name} is required when {context}")
    return raw_value.strip() if trim else raw_value


def _parse_bool(value: str | bool, default: bool = False) -> bool:
    """将环境变量布尔文本解析为标准布尔值。"""
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if not normalized:
        return default
    return normalized in {"1", "true", "yes", "on"}


def _parse_port(raw: str | int, *, env_name: str) -> int:
    """校验端口号处于 1..65535 的合法整数范围。"""
    try:
        port = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{env_name} must be an integer between 1 and 65535") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"{env_name} must be an integer between 1 and 65535")
    return port


def _mysql_database_url_from_env() -> str:
    """根据 MySQL 分项配置生成经过安全转义的 SQLAlchemy 连接地址。"""
    host = _required_env("MYSQL_HOST")
    database = _required_env("MYSQL_DATABASE")
    username = _required_env("MYSQL_USER")
    password = _required_env("MYSQL_PASSWORD", trim=False)
    port = _parse_port(_required_env("MYSQL_PORT"), env_name="MYSQL_PORT")

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
    """应用运行时不可变配置对象。"""

    database_url: str = "sqlite:///./ledger.db"
    echo_sql: bool = False
    log_level: str = "INFO"
    quote_provider: str = "eastmoney"
    quote_timeout_seconds: Decimal = Decimal("5")
    default_admin_email: str = DEFAULT_DEV_ADMIN_EMAIL
    auth_secret_key: str = DEFAULT_DEV_AUTH_SECRET
    auth_session_days: int = 30
    auth_cookie_name: str = "wing_session"
    auth_cookie_secure: bool = False
    email_provider: str = "console"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    frontend_dist_dir: str = ""

    def __post_init__(self) -> None:
        """校验直接构造或环境变量来源的日志、行情、认证与邮件配置。"""
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

        email_provider = self.email_provider.strip().lower()
        if email_provider not in SUPPORTED_EMAIL_PROVIDERS:
            allowed_email = ", ".join(sorted(SUPPORTED_EMAIL_PROVIDERS))
            raise ValueError(f"EMAIL_PROVIDER must be one of: {allowed_email}")

        session_days = int(self.auth_session_days)
        if session_days <= 0:
            raise ValueError("AUTH_SESSION_DAYS must be a positive integer")

        cookie_name = self.auth_cookie_name.strip()
        if not cookie_name:
            raise ValueError("AUTH_COOKIE_NAME must not be empty")

        admin_email = self.default_admin_email.strip().lower()
        if not admin_email:
            if email_provider == "smtp":
                raise ValueError("DEFAULT_ADMIN_EMAIL is required when EMAIL_PROVIDER=smtp")
            admin_email = DEFAULT_DEV_ADMIN_EMAIL
        if "@" not in admin_email or admin_email.startswith("@") or admin_email.endswith("@"):
            raise ValueError("DEFAULT_ADMIN_EMAIL must be a valid email address")

        secret_key = self.auth_secret_key.strip() or DEFAULT_DEV_AUTH_SECRET
        smtp_host = self.smtp_host.strip()
        smtp_port = _parse_port(self.smtp_port, env_name="SMTP_PORT")
        smtp_user = self.smtp_user.strip()
        smtp_from = self.smtp_from.strip() or smtp_user
        smtp_use_tls = bool(self.smtp_use_tls)
        smtp_use_ssl = bool(self.smtp_use_ssl)
        if smtp_use_tls and smtp_use_ssl:
            raise ValueError("SMTP_USE_TLS and SMTP_USE_SSL cannot both be true")
        if email_provider == "smtp":
            if not smtp_host:
                raise ValueError("SMTP_HOST is required when EMAIL_PROVIDER=smtp")
            if not smtp_from:
                raise ValueError("SMTP_FROM is required when EMAIL_PROVIDER=smtp")

        object.__setattr__(self, "log_level", log_level)
        object.__setattr__(self, "quote_provider", provider)
        object.__setattr__(self, "quote_timeout_seconds", timeout)
        object.__setattr__(self, "default_admin_email", admin_email)
        object.__setattr__(self, "auth_secret_key", secret_key)
        object.__setattr__(self, "auth_session_days", session_days)
        object.__setattr__(self, "auth_cookie_name", cookie_name)
        object.__setattr__(self, "auth_cookie_secure", bool(self.auth_cookie_secure))
        object.__setattr__(self, "email_provider", email_provider)
        object.__setattr__(self, "smtp_host", smtp_host)
        object.__setattr__(self, "smtp_port", smtp_port)
        object.__setattr__(self, "smtp_user", smtp_user)
        object.__setattr__(self, "smtp_from", smtp_from)
        object.__setattr__(self, "smtp_use_tls", smtp_use_tls)
        object.__setattr__(self, "smtp_use_ssl", smtp_use_ssl)
        object.__setattr__(self, "frontend_dist_dir", self.frontend_dist_dir.strip())

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

        email_provider = os.getenv("EMAIL_PROVIDER", defaults.email_provider).strip().lower()
        session_days_raw = os.getenv("AUTH_SESSION_DAYS", str(defaults.auth_session_days)).strip()
        try:
            auth_session_days = int(session_days_raw)
        except ValueError as exc:
            raise ValueError("AUTH_SESSION_DAYS must be a positive integer") from exc

        smtp_port = _parse_port(
            os.getenv("SMTP_PORT", str(defaults.smtp_port)).strip(),
            env_name="SMTP_PORT",
        )
        raw_use_tls = os.getenv("SMTP_USE_TLS", "").strip()
        raw_use_ssl = os.getenv("SMTP_USE_SSL", "").strip()
        if not raw_use_tls and not raw_use_ssl:
            # 465 端口默认为 SMTPS（隐式 SSL），其余端口（如 587）默认 STARTTLS。
            smtp_use_ssl = smtp_port == 465
            smtp_use_tls = not smtp_use_ssl
        elif raw_use_ssl and not raw_use_tls:
            smtp_use_ssl = _parse_bool(raw_use_ssl, default=False)
            smtp_use_tls = not smtp_use_ssl and smtp_port != 465
        elif raw_use_tls and not raw_use_ssl:
            smtp_use_tls = _parse_bool(raw_use_tls, default=True)
            smtp_use_ssl = False
        else:
            smtp_use_tls = _parse_bool(raw_use_tls, default=True)
            smtp_use_ssl = _parse_bool(raw_use_ssl, default=False)

        default_admin_email = os.getenv("DEFAULT_ADMIN_EMAIL", "").strip().lower()
        if not default_admin_email and email_provider == "console":
            default_admin_email = DEFAULT_DEV_ADMIN_EMAIL

        return cls(
            database_url=explicit_database_url or cls._database_url_from_env(defaults.database_url),
            echo_sql=_parse_bool(os.getenv("ECHO_SQL", "0")),
            log_level=os.getenv("LOG_LEVEL", defaults.log_level),
            quote_provider=quote_provider,
            quote_timeout_seconds=quote_timeout_seconds,
            default_admin_email=default_admin_email,
            auth_secret_key=os.getenv("AUTH_SECRET_KEY", defaults.auth_secret_key),
            auth_session_days=auth_session_days,
            auth_cookie_name=os.getenv("AUTH_COOKIE_NAME", defaults.auth_cookie_name),
            auth_cookie_secure=_parse_bool(os.getenv("AUTH_COOKIE_SECURE", "0")),
            email_provider=email_provider,
            smtp_host=os.getenv("SMTP_HOST", ""),
            smtp_port=smtp_port,
            smtp_user=os.getenv("SMTP_USER", ""),
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            smtp_from=os.getenv("SMTP_FROM", ""),
            smtp_use_tls=smtp_use_tls,
            smtp_use_ssl=smtp_use_ssl,
            frontend_dist_dir=os.getenv("FRONTEND_DIST_DIR", defaults.frontend_dist_dir),
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
