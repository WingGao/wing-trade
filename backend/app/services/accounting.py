from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from app.domain.exceptions import InvalidPriceError, InvalidQuantityError
from app.domain.models import (
    BuyCalculation,
    Fees,
    LowestPriceGroupMetrics,
    PositionMetrics,
    SellCalculation,
)
from app.domain.precision import ensure_decimal, round_amount, round_price, round_qty


class AccountingService:
    """Pure accounting formulas shared by persistence and API layers."""

    @staticmethod
    def fee_total(fees: Fees | None = None, **fee_values: object) -> Decimal:
        """校验兼容费用参数后返回精确手续费合计。"""
        if fees is None:
            fees = Fees(
                **{
                    key: ensure_decimal(value)
                    for key, value in fee_values.items()
                    if value is not None
                }
            )
        return fees.total

    @staticmethod
    def calculate_buy(
        qty: object, price: object, fees: Fees | None = None, **fee_values: object
    ) -> BuyCalculation:
        """校验精确数量、价格与费用并计算买入含费成本。"""
        quantity = round_qty(qty)
        unit_price = round_price(price)
        if quantity <= 0:
            raise InvalidQuantityError("quantity must be positive")
        if unit_price <= 0:
            raise InvalidPriceError("price must be positive")
        actual_fees = fees or Fees(
            **{key: ensure_decimal(value) for key, value in fee_values.items() if value is not None}
        )
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
    def calculate_sell(
        qty: object, price: object, fees: Fees | None = None, **fee_values: object
    ) -> SellCalculation:
        """校验精确数量、价格与费用并计算卖出净回款。"""
        quantity = round_qty(qty)
        unit_price = round_price(price)
        if quantity <= 0:
            raise InvalidQuantityError("quantity must be positive")
        if unit_price <= 0:
            raise InvalidPriceError("price must be positive")
        actual_fees = fees or Fees(
            **{key: ensure_decimal(value) for key, value in fee_values.items() if value is not None}
        )
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
        """计算一组批次的数量、成本、收益和回本参考指标。"""
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
            unrealized_return_pct=AccountingService.return_pct(unrealized, remaining_cost),
            strategy_breakeven_price=breakeven,
            funds_recovered_surplus=surplus,
        )

    @staticmethod
    def return_pct(profit: object | None, cost: object) -> Decimal | None:
        """在成本分母为正时按百分比单位计算收益率。"""
        denominator = round_amount(cost)
        if profit is None or denominator <= 0:
            return None
        return round_price(ensure_decimal(profit) / denominator * Decimal("100"))

    @staticmethod
    def lowest_price_group_metrics(
        lots: Iterable[object],
        *,
        latest_price: object | None = None,
        account_id: str | None = None,
        security_id: str | None = None,
        strategy_id: str | None = None,
    ) -> LowestPriceGroupMetrics | None:
        """合并最低原始买入成交价下的全部有效剩余批次并计算估值。"""
        lot_list = list(lots)
        eligible: list[object] = []
        for lot in lot_list:
            if getattr(lot, "status", None) != "OPEN":
                continue
            buy_trade = getattr(lot, "buy_trade", None)
            if (
                buy_trade is None
                or getattr(buy_trade, "status", None) != "ACTIVE"
                or getattr(buy_trade, "side", None) != "BUY"
            ):
                continue
            if round_qty(getattr(lot, "remaining_qty", 0)) <= 0:
                continue
            eligible.append(lot)
        if not eligible:
            return None

        scope_account = account_id or getattr(eligible[0], "account_id", None)
        scope_security = security_id or getattr(eligible[0], "security_id", None)
        scope_strategy = strategy_id or getattr(eligible[0], "strategy_id", None)
        eligible = [
            lot
            for lot in eligible
            if getattr(lot, "account_id", None) == scope_account
            and getattr(lot, "security_id", None) == scope_security
            and getattr(lot, "strategy_id", None) == scope_strategy
        ]
        if not eligible:
            return None

        prices = [
            round_price(getattr(getattr(lot, "buy_trade"), "price"))
            for lot in eligible
        ]
        lowest = min(prices)
        selected = [lot for lot, price in zip(eligible, prices, strict=True) if price == lowest]
        remaining_qty = round_qty(
            sum((ensure_decimal(getattr(lot, "remaining_qty")) for lot in selected), Decimal("0"))
        )
        remaining_cost = round_amount(
            sum((ensure_decimal(getattr(lot, "remaining_cost")) for lot in selected), Decimal("0"))
        )
        price = round_price(latest_price) if latest_price is not None else None
        unrealized = (
            round_amount(price * remaining_qty - remaining_cost)
            if price is not None and price > 0
            else None
        )
        return LowestPriceGroupMetrics(
            purchase_price=lowest,
            lot_count=len(selected),
            remaining_qty=remaining_qty,
            remaining_cost=remaining_cost,
            unrealized_pnl=unrealized,
            unrealized_return_pct=AccountingService.return_pct(unrealized, remaining_cost),
        )

