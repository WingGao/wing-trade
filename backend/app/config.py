"""应用配置：支持优先读取结构化 YAML 配置文件，其次回退环境变量与默认值。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.engine import URL

CONFIG_FILE_ENV_NAME = "CONFIG_FILE"
DEFAULT_CONFIG_FILENAMES = ("config.yaml", "config.yml")
SUPPORTED_DATABASE_BACKENDS = frozenset({"sqlite", "mysql"})
SUPPORTED_QUOTE_PROVIDERS = frozenset({"eastmoney", "mock"})
SUPPORTED_EMAIL_PROVIDERS = frozenset({"console", "smtp"})
SUPPORTED_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
DEFAULT_DEV_ADMIN_EMAIL = "admin@localhost"
DEFAULT_DEV_AUTH_SECRET = "wing-trade-dev-auth-secret-change-me"

_YAML_SCHEMA: dict[str, Any] = {
    "database": {
        "url": None,
        "backend": None,
        "sqlite_path": None,
        "echo_sql": None,
        "mysql": {
            "host": None,
            "port": None,
            "database": None,
            "user": None,
            "password": None,
        },
    },
    "logging": {
        "level": None,
    },
    "quote": {
        "provider": None,
        "timeout_seconds": None,
    },
    "auth": {
        "default_admin_email": None,
        "secret_key": None,
        "session_days": None,
        "cookie_name": None,
        "cookie_secure": None,
    },
    "email": {
        "provider": None,
        "smtp": {
            "host": None,
            "port": None,
            "user": None,
            "password": None,
            "from": None,
            "use_tls": None,
            "use_ssl": None,
        },
    },
    "frontend": {
        "dist_dir": None,
    },
}


def normalize_log_level(value: str) -> str:
    """将日志等级规范为大写，并拒绝 Python 标准等级之外的值。"""
    normalized = value.strip().upper()
    if normalized not in SUPPORTED_LOG_LEVELS:
        allowed = ", ".join(sorted(SUPPORTED_LOG_LEVELS))
        raise ValueError(f"LOG_LEVEL must be one of: {allowed}")
    return normalized


def _default_config_candidates() -> tuple[Path, ...]:
    """返回默认 YAML 配置文件候选路径（当前工作目录优先，其次项目根目录）。"""
    repo_root = Path(__file__).resolve().parents[2]
    search_dirs = [Path.cwd(), repo_root]
    candidates: list[Path] = []
    seen: set[Path] = set()
    for directory in search_dirs:
        for filename in DEFAULT_CONFIG_FILENAMES:
            candidate = directory / filename
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                candidates.append(candidate)
    return tuple(candidates)


def _resolve_config_path(
    config_file: str | os.PathLike[str] | None = None,
    *,
    discover_default: bool = True,
) -> Path | None:
    """按显式参数、CONFIG_FILE 环境变量和默认候选路径顺序解析 YAML 配置文件位置。"""
    if config_file is not None:
        raw_path = os.fspath(config_file).strip()
        if raw_path:
            explicit = Path(raw_path)
            if not explicit.is_file():
                raise ValueError(f"Config file not found: {explicit}")
            return explicit

    env_path = os.getenv(CONFIG_FILE_ENV_NAME, "").strip()
    if env_path:
        explicit = Path(env_path)
        if not explicit.is_file():
            raise ValueError(f"{CONFIG_FILE_ENV_NAME} not found: {explicit}")
        return explicit

    if not discover_default:
        return None

    for candidate in _default_config_candidates():
        if candidate.is_file():
            return candidate
    return None


def _validate_yaml_mapping(
    data: dict[Any, Any],
    schema: dict[str, Any],
    *,
    prefix: str = "",
) -> dict[str, Any]:
    """按白名单递归校验结构化 YAML 的分组与字段名称，拒绝未知键与非法嵌套。"""
    validated: dict[str, Any] = {}
    for raw_key, value in data.items():
        if not isinstance(raw_key, str):
            location = f"{prefix}.{raw_key}" if prefix else str(raw_key)
            raise ValueError(f"Config key must be a string: {location}")
        key_path = f"{prefix}.{raw_key}" if prefix else raw_key
        if raw_key not in schema:
            raise ValueError(f"Unknown config key: {key_path}")
        expected = schema[raw_key]
        if isinstance(expected, dict):
            if value is None:
                validated[raw_key] = {}
            elif isinstance(value, dict):
                validated[raw_key] = _validate_yaml_mapping(value, expected, prefix=key_path)
            else:
                raise ValueError(f"Config section '{key_path}' must be a mapping")
        else:
            if isinstance(value, (dict, list)):
                raise ValueError(f"Config value '{key_path}' must be a scalar")
            validated[raw_key] = value
    return validated


def _load_yaml_config(path: Path) -> dict[str, Any]:
    """读取并解析指定 YAML 配置文件，空文件返回空字典。"""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Failed to read config file '{path}': {exc}") from exc

    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML syntax in '{path}': {exc}") from exc

    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ValueError(f"Config file '{path}' root must be a YAML mapping")
    return _validate_yaml_mapping(parsed, _YAML_SCHEMA)


def _yaml_get(config: dict[str, Any], path: tuple[str, ...]) -> Any:
    """按嵌套路径从已校验 YAML 字典读取值，任一层缺失或为 None 时返回 None。"""
    current: Any = config
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
        if current is None:
            return None
    return current


def _resolve_raw(config: dict[str, Any], path: tuple[str, ...], env_name: str) -> Any:
    """按 YAML 优先、环境变量其次的顺序读取原始配置值。"""
    yaml_value = _yaml_get(config, path)
    if yaml_value is not None:
        return yaml_value
    return os.getenv(env_name)


def _resolve_str(
    config: dict[str, Any],
    path: tuple[str, ...],
    env_name: str,
    default: str = "",
    *,
    trim: bool = True,
    empty_as_missing: bool = False,
) -> str:
    """按 YAML > env > 默认值顺序读取字符串配置，可选将空字符串视为未提供。"""
    yaml_value = _yaml_get(config, path)
    if yaml_value is not None:
        if isinstance(yaml_value, bool):
            raise ValueError(f"{env_name} must be a string")
        text = str(yaml_value)
        normalized = text.strip() if trim else text
        if not empty_as_missing or normalized != "":
            return normalized

    env_value = os.getenv(env_name)
    if env_value is not None:
        normalized = env_value.strip() if trim else env_value
        if not empty_as_missing or normalized != "":
            return normalized
    return default


def _required_config_value(
    config: dict[str, Any],
    path: tuple[str, ...],
    env_name: str,
    *,
    trim: bool = True,
    context: str = "DATABASE_BACKEND=mysql",
) -> str:
    """读取必填字符串配置（YAML 优先，其次 env），拒绝缺失或空值。"""
    value = _resolve_str(config, path, env_name, default="", trim=trim, empty_as_missing=True)
    if not value:
        raise ValueError(f"{env_name} is required when {context}")
    return value


def _parse_bool(value: Any, default: bool = False, *, field_name: str | None = None) -> bool:
    """将 YAML 原生布尔或环境变量布尔文本解析为标准布尔值。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if not normalized:
            return default
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        label = field_name or "Boolean setting"
        raise ValueError(f"{label} must be a boolean")
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    label = field_name or "Boolean setting"
    raise ValueError(f"{label} must be a boolean")


def _resolve_bool(
    config: dict[str, Any],
    path: tuple[str, ...],
    env_name: str,
    *,
    default: bool = False,
) -> bool:
    """按 YAML > env > 默认值顺序解析布尔配置，确保 false 不会回退 env。"""
    raw = _resolve_raw(config, path, env_name)
    if raw is None:
        return default
    return _parse_bool(raw, default=default, field_name=env_name)


def _resolve_optional_bool(
    config: dict[str, Any],
    path: tuple[str, ...],
    env_name: str,
) -> bool | None:
    """读取可选布尔配置，区分显式配置（含 false）与未配置（None）。"""
    yaml_value = _yaml_get(config, path)
    if yaml_value is not None:
        if isinstance(yaml_value, str) and not yaml_value.strip():
            yaml_value = None
        else:
            return _parse_bool(yaml_value, field_name=env_name)

    env_value = os.getenv(env_name)
    if env_value is None or not env_value.strip():
        return None
    return _parse_bool(env_value, field_name=env_name)


def _parse_port(raw: Any, *, env_name: str) -> int:
    """校验端口号处于 1..65535 的合法整数范围，并拒绝布尔值。"""
    if isinstance(raw, bool):
        raise ValueError(f"{env_name} must be an integer between 1 and 65535")
    try:
        port = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{env_name} must be an integer between 1 and 65535") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"{env_name} must be an integer between 1 and 65535")
    return port


def _parse_positive_int(raw: Any, *, field_name: str) -> int:
    """解析正整数配置并拒绝布尔值或非正整数。"""
    if isinstance(raw, bool):
        raise ValueError(f"{field_name} must be a positive integer")
    try:
        parsed = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return parsed


def _parse_positive_decimal(raw: Any, *, field_name: str) -> Decimal:
    """解析有限正十进制数配置并拒绝布尔值或非法数值。"""
    if isinstance(raw, bool):
        raise ValueError(f"{field_name} must be a finite positive number")
    try:
        parsed = raw if isinstance(raw, Decimal) else Decimal(str(raw).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite positive number") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError(f"{field_name} must be a finite positive number")
    return parsed


def _mysql_database_url_from_sources(config: dict[str, Any]) -> str:
    """按 YAML 优先、env 其次读取 MySQL 分项配置并生成安全转义的连接地址。"""
    host = _required_config_value(config, ("database", "mysql", "host"), "MYSQL_HOST")
    database = _required_config_value(config, ("database", "mysql", "database"), "MYSQL_DATABASE")
    username = _required_config_value(config, ("database", "mysql", "user"), "MYSQL_USER")
    password = _required_config_value(
        config,
        ("database", "mysql", "password"),
        "MYSQL_PASSWORD",
        trim=False,
    )
    raw_port = _resolve_raw(config, ("database", "mysql", "port"), "MYSQL_PORT")
    if raw_port is None or (isinstance(raw_port, str) and not raw_port.strip()):
        raise ValueError("MYSQL_PORT is required when DATABASE_BACKEND=mysql")
    port = _parse_port(raw_port, env_name="MYSQL_PORT")

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
        """校验直接构造、YAML 或环境变量来源的日志、行情、认证与邮件配置。"""
        log_level = normalize_log_level(self.log_level)
        provider = self.quote_provider.strip().lower()
        if provider not in SUPPORTED_QUOTE_PROVIDERS:
            allowed = ", ".join(sorted(SUPPORTED_QUOTE_PROVIDERS))
            raise ValueError(f"QUOTE_PROVIDER must be one of: {allowed}")
        timeout = _parse_positive_decimal(
            self.quote_timeout_seconds,
            field_name="QUOTE_TIMEOUT_SECONDS",
        )

        email_provider = self.email_provider.strip().lower()
        if email_provider not in SUPPORTED_EMAIL_PROVIDERS:
            allowed_email = ", ".join(sorted(SUPPORTED_EMAIL_PROVIDERS))
            raise ValueError(f"EMAIL_PROVIDER must be one of: {allowed_email}")

        session_days = _parse_positive_int(self.auth_session_days, field_name="AUTH_SESSION_DAYS")

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
    def load(
        cls,
        config_file: str | os.PathLike[str] | None = None,
        *,
        discover_default: bool = True,
    ) -> Settings:
        """按 YAML 优先、环境变量其次、默认值兜底的顺序加载并校验应用配置。"""
        defaults = cls()
        resolved_path = _resolve_config_path(config_file, discover_default=discover_default)
        yaml_config = _load_yaml_config(resolved_path) if resolved_path is not None else {}

        quote_provider = _resolve_str(
            yaml_config,
            ("quote", "provider"),
            "QUOTE_PROVIDER",
            default=defaults.quote_provider,
            empty_as_missing=True,
        ).lower()
        if quote_provider not in SUPPORTED_QUOTE_PROVIDERS:
            allowed = ", ".join(sorted(SUPPORTED_QUOTE_PROVIDERS))
            raise ValueError(f"QUOTE_PROVIDER must be one of: {allowed}")

        raw_timeout = _resolve_raw(
            yaml_config,
            ("quote", "timeout_seconds"),
            "QUOTE_TIMEOUT_SECONDS",
        )
        quote_timeout_seconds = (
            defaults.quote_timeout_seconds
            if raw_timeout is None or (isinstance(raw_timeout, str) and not raw_timeout.strip())
            else _parse_positive_decimal(raw_timeout, field_name="QUOTE_TIMEOUT_SECONDS")
        )

        email_provider = _resolve_str(
            yaml_config,
            ("email", "provider"),
            "EMAIL_PROVIDER",
            default=defaults.email_provider,
            empty_as_missing=True,
        ).lower()

        raw_session_days = _resolve_raw(
            yaml_config,
            ("auth", "session_days"),
            "AUTH_SESSION_DAYS",
        )
        auth_session_days = (
            defaults.auth_session_days
            if raw_session_days is None
            or (isinstance(raw_session_days, str) and not raw_session_days.strip())
            else _parse_positive_int(raw_session_days, field_name="AUTH_SESSION_DAYS")
        )

        raw_smtp_port = _resolve_raw(yaml_config, ("email", "smtp", "port"), "SMTP_PORT")
        smtp_port = (
            defaults.smtp_port
            if raw_smtp_port is None
            or (isinstance(raw_smtp_port, str) and not raw_smtp_port.strip())
            else _parse_port(raw_smtp_port, env_name="SMTP_PORT")
        )

        explicit_use_tls = _resolve_optional_bool(
            yaml_config,
            ("email", "smtp", "use_tls"),
            "SMTP_USE_TLS",
        )
        explicit_use_ssl = _resolve_optional_bool(
            yaml_config,
            ("email", "smtp", "use_ssl"),
            "SMTP_USE_SSL",
        )
        if explicit_use_tls is None and explicit_use_ssl is None:
            # 465 端口默认为 SMTPS（隐式 SSL），其余端口（如 587）默认 STARTTLS。
            smtp_use_ssl = smtp_port == 465
            smtp_use_tls = not smtp_use_ssl
        elif explicit_use_ssl is not None and explicit_use_tls is None:
            smtp_use_ssl = explicit_use_ssl
            smtp_use_tls = not smtp_use_ssl and smtp_port != 465
        elif explicit_use_tls is not None and explicit_use_ssl is None:
            smtp_use_tls = explicit_use_tls
            smtp_use_ssl = False
        else:
            smtp_use_tls = bool(explicit_use_tls)
            smtp_use_ssl = bool(explicit_use_ssl)

        default_admin_email = _resolve_str(
            yaml_config,
            ("auth", "default_admin_email"),
            "DEFAULT_ADMIN_EMAIL",
            default="",
            empty_as_missing=True,
        ).lower()
        if not default_admin_email and email_provider == "console":
            default_admin_email = DEFAULT_DEV_ADMIN_EMAIL

        return cls(
            database_url=cls._database_url_from_sources(yaml_config, defaults.database_url),
            echo_sql=_resolve_bool(
                yaml_config,
                ("database", "echo_sql"),
                "ECHO_SQL",
                default=False,
            ),
            log_level=_resolve_str(
                yaml_config,
                ("logging", "level"),
                "LOG_LEVEL",
                default=defaults.log_level,
                empty_as_missing=True,
            ),
            quote_provider=quote_provider,
            quote_timeout_seconds=quote_timeout_seconds,
            default_admin_email=default_admin_email,
            auth_secret_key=_resolve_str(
                yaml_config,
                ("auth", "secret_key"),
                "AUTH_SECRET_KEY",
                default=defaults.auth_secret_key,
                empty_as_missing=True,
            ),
            auth_session_days=auth_session_days,
            auth_cookie_name=_resolve_str(
                yaml_config,
                ("auth", "cookie_name"),
                "AUTH_COOKIE_NAME",
                default=defaults.auth_cookie_name,
                empty_as_missing=True,
            ),
            auth_cookie_secure=_resolve_bool(
                yaml_config,
                ("auth", "cookie_secure"),
                "AUTH_COOKIE_SECURE",
                default=False,
            ),
            email_provider=email_provider,
            smtp_host=_resolve_str(
                yaml_config,
                ("email", "smtp", "host"),
                "SMTP_HOST",
                default="",
                empty_as_missing=True,
            ),
            smtp_port=smtp_port,
            smtp_user=_resolve_str(
                yaml_config,
                ("email", "smtp", "user"),
                "SMTP_USER",
                default="",
                empty_as_missing=True,
            ),
            smtp_password=_resolve_str(
                yaml_config,
                ("email", "smtp", "password"),
                "SMTP_PASSWORD",
                default="",
                trim=False,
                empty_as_missing=True,
            ),
            smtp_from=_resolve_str(
                yaml_config,
                ("email", "smtp", "from"),
                "SMTP_FROM",
                default="",
                empty_as_missing=True,
            ),
            smtp_use_tls=smtp_use_tls,
            smtp_use_ssl=smtp_use_ssl,
            frontend_dist_dir=_resolve_str(
                yaml_config,
                ("frontend", "dist_dir"),
                "FRONTEND_DIST_DIR",
                default=defaults.frontend_dist_dir,
                empty_as_missing=True,
            ),
        )

    @classmethod
    def from_env(cls) -> Settings:
        """兼容入口：按 YAML 优先、环境变量其次加载并校验应用配置。"""
        return cls.load()

    @staticmethod
    def _database_url_from_sources(config: dict[str, Any], default_url: str) -> str:
        """按 YAML 优先、env 其次解析数据库连接地址，并保留 SQLite 原默认行为。"""
        explicit_database_url = _resolve_str(
            config,
            ("database", "url"),
            "DATABASE_URL",
            default="",
            empty_as_missing=True,
        )
        if explicit_database_url:
            return explicit_database_url

        backend = _resolve_str(
            config,
            ("database", "backend"),
            "DATABASE_BACKEND",
            default="sqlite",
            empty_as_missing=True,
        ).lower()
        if backend not in SUPPORTED_DATABASE_BACKENDS:
            allowed = ", ".join(sorted(SUPPORTED_DATABASE_BACKENDS))
            raise ValueError(f"DATABASE_BACKEND must be one of: {allowed}")
        if backend == "mysql":
            return _mysql_database_url_from_sources(config)

        sqlite_path = _resolve_str(
            config,
            ("database", "sqlite_path"),
            "DATABASE_SQLITE_PATH",
            default="",
            empty_as_missing=True,
        )
        if not sqlite_path:
            return default_url
        return URL.create(drivername="sqlite", database=sqlite_path).render_as_string(
            hide_password=False
        )


settings = Settings.load()
