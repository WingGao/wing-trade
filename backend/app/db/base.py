from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    """Base for all SQLAlchemy models."""


class ExactDecimal(TypeDecorator[Decimal]):
    """Store fixed-scale Decimal values as text on SQLite.

    SQLite's NUMERIC affinity may coerce values into binary floats.  Text
    storage preserves every accounting digit and still lets the domain layer
    perform all comparisons and calculations with Decimal.
    """

    impl = String
    cache_ok = True

    def __init__(self, scale: int, length: int = 32) -> None:
        self.scale = scale
        self.length = length
        super().__init__(length)

    def _quantum(self) -> Decimal:
        return Decimal(1).scaleb(-self.scale)

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, float):
            raise TypeError("ExactDecimal does not accept float values")
        try:
            decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
            if not decimal_value.is_finite():
                raise ValueError("decimal value must be finite")
            return format(decimal_value.quantize(self._quantum(), rounding=ROUND_HALF_UP), f".{self.scale}f")
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"invalid decimal value: {value!r}") from exc

    def process_result_value(self, value: Any, dialect: Any) -> Decimal | None:
        if value is None:
            return None
        return Decimal(str(value)).quantize(self._quantum(), rounding=ROUND_HALF_UP)

