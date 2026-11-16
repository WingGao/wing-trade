from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from .common import APIModel


class QuoteRead(APIModel):
    """首页使用的统一行情快照。"""

    symbol: str
    security_id: str | None = None
    market: str | None = None
    currency: str | None = None
    last_price: Decimal | None = None
    prev_close: Decimal | None = None
    change_pct: Decimal | None = None
    source_time: datetime | None = None
    fetched_at: datetime | None = None
    provider: str | None = None
    is_stale: bool = True


class LowestPriceGroupRead(APIModel):
    """最低原始买入价下全部剩余批次的合并估值。"""

    purchase_price: Decimal
    lot_count: int
    remaining_qty: Decimal
    remaining_cost: Decimal
    unrealized_pnl: Decimal | None = None
    unrealized_return_pct: Decimal | None = None


class DashboardPositionRead(APIModel):
    """首页持仓行及其同次行情估值。"""

    security_id: str
    market: str | None = None
    symbol: str | None = None
    name: str | None = None
    currency: str | None = None
    strategy_id: str
    remaining_qty: Decimal
    remaining_cost: Decimal
    average_cost: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal | None = None
    unrealized_return_pct: Decimal | None = None
    strategy_breakeven_price: Decimal | None = None
    funds_recovered_surplus: Decimal | None = None
    quote: QuoteRead | None = None
    lowest_price_group: LowestPriceGroupRead | None = None


class DashboardSummaryRead(APIModel):
    """首页账户汇总及行情覆盖状态。"""

    currency: str
    total_buy_cash_out: Decimal
    total_sell_cash_in: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal | None = None
    unrealized_return_pct: Decimal | None = None
    remaining_qty: Decimal
    remaining_cost: Decimal
    valuation_status: Literal["fresh", "stale", "partial", "unavailable", "empty"]
    total_open_positions: int
    quoted_open_positions: int
    stale_open_positions: int


class DashboardRead(APIModel):
    """首页账务、持仓、行情和汇总的一致读取模型。"""

    account_id: str
    currency: str
    as_of: datetime
    market_session_open: bool
    poll_after_seconds: Literal[10, 60]
    positions: list[DashboardPositionRead] = Field(default_factory=list)
    summary: DashboardSummaryRead
    quotes: list[QuoteRead] = Field(default_factory=list)
