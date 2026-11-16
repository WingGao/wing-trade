"""Application configuration kept deliberately small for the domain package."""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

SUPPORTED_QUOTE_PROVIDERS = frozenset({"eastmoney", "mock"})


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str = "sqlite:///./ledger.db"
    echo_sql: bool = False
    quote_provider: str = "eastmoney"
    quote_timeout_seconds: Decimal = Decimal("5")

    def __post_init__(self) -> None:
        """校验直接构造或环境变量来源的行情配置。"""
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

    @classmethod
    def from_env(cls) -> Settings:
        """从环境变量读取并校验应用配置。"""
        defaults = cls()
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
            database_url=os.getenv("DATABASE_URL", defaults.database_url),
            echo_sql=os.getenv("ECHO_SQL", "0").lower() in {"1", "true", "yes"},
            quote_provider=quote_provider,
            quote_timeout_seconds=quote_timeout_seconds,
        )


settings = Settings.from_env()
