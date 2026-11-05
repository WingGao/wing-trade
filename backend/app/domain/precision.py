"""Decimal-only accounting precision helpers.

The ledger rejects binary floats at its boundary.  Accepting a float and
converting it through ``str`` can hide a caller bug, so callers must provide a
string, integer, Decimal, or another explicitly supported exact value.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

QTY_SCALE = Decimal("0.0001")
PRICE_SCALE = Decimal("0.000001")
AMOUNT_SCALE = Decimal("0.0001")


def ensure_decimal(value: Any, *, field: str = "value") -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError(f"{field} must be an exact Decimal-compatible value, not float")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"{field} must be finite")
        return value
    if isinstance(value, (str, int)):
        try:
            decimal_value = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"{field} is not a valid decimal") from exc
        if not decimal_value.is_finite():
            raise ValueError(f"{field} must be finite")
        return decimal_value
    raise TypeError(f"{field} must be Decimal, str, or int")


def _quantize(value: Any, scale: Decimal, *, field: str) -> Decimal:
    return ensure_decimal(value, field=field).quantize(scale, rounding=ROUND_HALF_UP)


def round_qty(value: Any) -> Decimal:
    return _quantize(value, QTY_SCALE, field="quantity")


def round_price(value: Any) -> Decimal:
    return _quantize(value, PRICE_SCALE, field="price")


def round_amount(value: Any) -> Decimal:
    return _quantize(value, AMOUNT_SCALE, field="amount")


def sum_amount(*values: Any) -> Decimal:
    return round_amount(sum((ensure_decimal(v) for v in values), Decimal("0")))
