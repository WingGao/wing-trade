from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from .enums import CalcBasis, MatchingMethod
from .exceptions import NegativeFeeError
from .precision import round_amount, round_price, round_qty, ensure_decimal


@dataclass(frozen=True, slots=True)
class Fees:
    commission: Decimal = Decimal("0")
    stamp_duty: Decimal = Decimal("0")
    transfer_fee: Decimal = Decimal("0")
    other_fee: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        for name in ("commission", "stamp_duty", "transfer_fee", "other_fee"):
            value = ensure_decimal(getattr(self, name), field=name)
            if value < 0:
                raise NegativeFeeError(f"{name} must not be negative", details={"field": name})
            object.__setattr__(self, name, round_amount(value))

    @property
    def total(self) -> Decimal:
        return round_amount(self.commission + self.stamp_duty + self.transfer_fee + self.other_fee)


@dataclass(frozen=True, slots=True)
class BuyCalculation:
    qty: Decimal
    price: Decimal
    gross: Decimal
    fees: Fees
    cash_out: Decimal
    unit_cost: Decimal


@dataclass(frozen=True, slots=True)
class SellCalculation:
    qty: Decimal
    price: Decimal
    gross: Decimal
    fees: Fees
    cash_in: Decimal


@dataclass(frozen=True, slots=True)
class LotSnapshot:
    id: str
    account_id: str
    security_id: str
    strategy_id: str
    original_qty: Decimal
    original_cost: Decimal
    remaining_qty: Decimal
    remaining_cost: Decimal
    executed_at: datetime
    created_at: datetime | None = None

    @property
    def status(self) -> str:
        return "CLOSED" if self.remaining_qty == 0 else "OPEN"


@dataclass(frozen=True, slots=True)
class MatchRequest:
    lot_id: str
    matched_qty: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "matched_qty", round_qty(self.matched_qty))


@dataclass(frozen=True, slots=True)
class MatchAllocation:
    lot_id: str
    matched_qty: Decimal
    allocated_buy_cost: Decimal
    allocated_sell_net: Decimal
    realized_pnl: Decimal
    method: MatchingMethod = MatchingMethod.MANUAL


@dataclass(frozen=True, slots=True)
class PositionMetrics:
    remaining_qty: Decimal
    remaining_cost: Decimal
    average_cost: Decimal
    realized_pnl: Decimal = Decimal("0.0000")
    unrealized_pnl: Decimal | None = None
    strategy_breakeven_price: Decimal | None = None
    funds_recovered_surplus: Decimal | None = None


@dataclass(frozen=True, slots=True)
class TargetPriceInput:
    unit_cost: Decimal
    planned_sell_qty: Decimal
    target_return_pct: Decimal
    commission_rate: Decimal = Decimal("0")
    minimum_commission: Decimal = Decimal("0")
    stamp_duty_rate: Decimal = Decimal("0")
    transfer_fee_rate: Decimal = Decimal("0")
    basis: CalcBasis = CalcBasis.NET

