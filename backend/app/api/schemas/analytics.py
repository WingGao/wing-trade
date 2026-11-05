from __future__ import annotations

from decimal import Decimal

from .common import APIModel


class AnalyticsSummaryRead(APIModel):
    total_buy_cash_out: Decimal
    total_sell_cash_in: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal | None = None
    remaining_qty: Decimal
    remaining_cost: Decimal

