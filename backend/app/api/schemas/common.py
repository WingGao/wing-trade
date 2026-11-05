from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


def to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


class APIModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)

    @field_validator("*", mode="before")
    @classmethod
    def reject_binary_float(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("JSON floating point values are not accepted for exact ledger fields")
        return value


class ErrorBody(APIModel):
    code: str
    message: str
    details: dict[str, Any] = {}


class ErrorResponse(APIModel):
    error: ErrorBody


def decimal_string(value: Decimal | None, scale: int = 4) -> str | None:
    if value is None:
        return None
    return f"{value:.{scale}f}"

