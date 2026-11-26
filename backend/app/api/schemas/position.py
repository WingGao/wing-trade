from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from .common import APIModel


class TradeMatchRead(APIModel):
    id: str
    sell_trade_id: str
    lot_id: str
    matched_qty: Decimal
    allocated_buy_cost: Decimal
    allocated_sell_net: Decimal
    realized_pnl: Decimal
    matching_method: str
    created_at: datetime


class SettledTradeDetailRead(APIModel):
    """已结清批次的原始买入或本批卖出分摊，不重复计算整笔跨批成交。"""

    lot_id: str
    trade_id: str
    side: Literal["BUY", "SELL"]
    executed_at: datetime
    qty: Decimal
    original_trade_qty: Decimal
    price: Decimal
    cash_amount: Decimal
    realized_pnl: Decimal | None = None


class LotRead(APIModel):
    id: str
    buy_trade_id: str
    account_id: str
    security_id: str
    strategy_id: str
    cycle_id: str
    original_qty: Decimal
    original_cost: Decimal
    remaining_qty: Decimal
    remaining_cost: Decimal
    unit_cost: Decimal
    purchase_price: Decimal | None = None
    executed_at: datetime
    status: str
    targets: list[dict] = []


class CycleRead(APIModel):
    id: str
    account_id: str
    security_id: str
    strategy_id: str
    opened_at: datetime
    closed_at: datetime | None = None
    status: str


class PositionRead(APIModel):
    security_id: str
    strategy_id: str
    remaining_qty: Decimal
    remaining_cost: Decimal
    average_cost: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal | None = None
    strategy_breakeven_price: Decimal | None = None
    funds_recovered_surplus: Decimal | None = None

