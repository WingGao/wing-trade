from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import APIModel


class AccountCreate(APIModel):
    name: str = Field(min_length=1, max_length=128)
    currency: Literal["CNY", "USD"] = "CNY"
    broker: str | None = None


class AccountRead(APIModel):
    id: str
    name: str
    currency: str
    broker: str | None = None
