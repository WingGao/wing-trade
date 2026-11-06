"""Application configuration kept deliberately small for the domain package."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str = "sqlite:///./ledger.db"
    echo_sql: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        defaults = cls()
        return cls(
            database_url=os.getenv("DATABASE_URL", defaults.database_url),
            echo_sql=os.getenv("ECHO_SQL", "0").lower() in {"1", "true", "yes"},
        )


settings = Settings.from_env()
