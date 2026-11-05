from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from app.domain.models import BuyCalculation, Fees, PositionMetrics, SellCalculation
from app.domain.exceptions import InvalidPriceError, InvalidQuantityError
from app.domain.precision import ensure_decimal, round_amount, round_price, round_qty


class AccountingService:
    """Pure accounting formulas shared by persistence and API layers."""

    @staticmethod
    def fee_total(fees: Fees | None = None, **fee_values: object) -> Decimal:
        if fees is None:
            fees = Fees(**{key: value for key, value in fee_values.items() if value is not None})
        return fees.total

    @staticmethod
    def calculate_buy(qty: object, price: object, fees: Fees | None = None, **fee_values: object) -> BuyCalculation:
        quantity = round_qty(qty)
        unit_price = round_price(price)
        if quantity <= 0:
            raise InvalidQuantityError("quantity must be positive")
        if unit_price <= 0:
            raise InvalidPriceError("price must be positive")
        actual_fees = fees or Fees(**{key: value for key, value in fee_values.items() if value is not None})
        gross = round_amount(unit_price * quantity)
        cash_out = round_amount(gross + actual_fees.total)
        return BuyCalculation(
            qty=quantity,
            price=unit_price,
            gross=gross,
            fees=actual_fees,
            cash_out=cash_out,
            unit_cost=round_price(cash_out / quantity),
        )

    @staticmethod
    def calculate_sell(qty: object, price: object, fees: Fees | None = None, **fee_values: object) -> SellCalculation:
        quantity = round_qty(qty)
        unit_price = round_price(price)
        if quantity <= 0:
            raise InvalidQuantityError("quantity must be positive")
        if unit_price <= 0:
            raise InvalidPriceError("price must be positive")
        actual_fees = fees or Fees(**{key: value for key, value in fee_values.items() if value is not None})
        gross = round_amount(unit_price * quantity)
        cash_in = round_amount(gross - actual_fees.total)
        return SellCalculation(
            qty=quantity,
            price=unit_price,
            gross=gross,
            fees=actual_fees,
            cash_in=cash_in,
        )

    @staticmethod
    def buy_cash_out(qty: object, price: object, fees: Fees | None = None, **fee_values: object) -> Decimal:
        return AccountingService.calculate_buy(qty, price, fees, **fee_values).cash_out

    @staticmethod
    def sell_cash_in(qty: object, price: object, fees: Fees | None = None, **fee_values: object) -> Decimal:
        return AccountingService.calculate_sell(qty, price, fees, **fee_values).cash_in

    @staticmethod
    def allocate_buy_cost(
        original_qty: object,
        original_cost: object,
        remaining_qty: object,
        matched_qty: object,
        remaining_cost: object | None = None,
    ) -> Decimal:
        original_quantity = round_qty(original_qty)
        original_amount = round_amount(original_cost)
        remaining_quantity = round_qty(remaining_qty)
        quantity = round_qty(matched_qty)
        current_cost = original_amount if remaining_cost is None else round_amount(remaining_cost)
        if quantity <= 0 or quantity > remaining_quantity:
            raise ValueError("matched quantity must be positive and no greater than remaining quantity")
        if quantity == remaining_quantity:
            return current_cost
        return round_amount(original_amount * quantity / original_quantity)

    @staticmethod
    def allocate_sell_net(sell_cash_in: object, sell_qty: object, matched_qty: object) -> Decimal:
        net = round_amount(sell_cash_in)
        total_qty = round_qty(sell_qty)
        quantity = round_qty(matched_qty)
        if total_qty <= 0 or quantity <= 0 or quantity > total_qty:
            raise ValueError("matched quantity must be within sell quantity")
        return round_amount(net * quantity / total_qty)

    @staticmethod
    def position_metrics(
        lots: Iterable[object],
        *,
        latest_price: object | None = None,
        realized_pnl: object = Decimal("0"),
        total_buy_cash_out: object | None = None,
        total_sell_cash_in: object | None = None,
    ) -> PositionMetrics:
        lot_list = list(lots)
        remaining_qty = round_qty(
            sum((ensure_decimal(getattr(lot, "remaining_qty")) for lot in lot_list), Decimal("0"))
        )
        remaining_cost = round_amount(
            sum((ensure_decimal(getattr(lot, "remaining_cost")) for lot in lot_list), Decimal("0"))
        )
        average_cost = round_price(remaining_cost / remaining_qty) if remaining_qty else Decimal("0.000000")
        unrealized = None
        if latest_price is not None:
            unrealized = round_amount(remaining_qty * round_price(latest_price) - remaining_cost)
        breakeven = None
        surplus = None
        if total_buy_cash_out is not None and total_sell_cash_in is not None and remaining_qty:
            unrecovered = round_amount(ensure_decimal(total_buy_cash_out) - ensure_decimal(total_sell_cash_in))
            if unrecovered >= 0:
                breakeven = round_price(unrecovered / remaining_qty)
            else:
                surplus = round_amount(-unrecovered)
        return PositionMetrics(
            remaining_qty=remaining_qty,
            remaining_cost=remaining_cost,
            average_cost=average_cost,
            realized_pnl=round_amount(realized_pnl),
            unrealized_pnl=unrealized,
            strategy_breakeven_price=breakeven,
            funds_recovered_surplus=surplus,
        )

