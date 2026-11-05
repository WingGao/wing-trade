from __future__ import annotations

from pydantic import Field

from .common import APIModel


class SecurityCreate(APIModel):
    market: str = Field(min_length=1, max_length=8)
    symbol: str = Field(min_length=1, max_length=32)
    name: str | None = None
    security_type: str = "STOCK"


class SecurityRead(APIModel):
    id: str
    market: str
    symbol: str
    name: str | None = None
    security_type: str
    status: str


class WatchlistItemCreate(APIModel):
    security_id: str
    sort_order: int = 0
    account_id: str | None = None


class WatchlistItemRead(APIModel):
    id: str
    account_id: str
    security_id: str
    sort_order: int
    security: SecurityRead | None = None

