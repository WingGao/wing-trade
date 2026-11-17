from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.domain.enums import MatchingMethod

from .common import APIModel


class FeeFields(APIModel):
    commission: Decimal = Decimal("0")
    stamp_duty: Decimal = Decimal("0")
    transfer_fee: Decimal = Decimal("0")
    other_fee: Decimal = Decimal("0")


class BuyTradeRequest(FeeFields):
    fees: FeeFields | None = None
    account_id: str | None = None
    security_id: str
    strategy_id: str | None = None
    executed_at: datetime
    qty: Decimal
    price: Decimal
    client_request_id: str | None = None
    source: str = "MANUAL"
    external_trade_id: str | None = None
    import_batch_id: str | None = None
    memo: str | None = None


class MatchRequestRead(APIModel):
    lot_id: str
    matched_qty: Decimal | None = None
    qty: Decimal | None = None


class SellPreviewRequest(BuyTradeRequest):
    matches: list[MatchRequestRead] | None = None
    matching_method: MatchingMethod | None = None


class SellTradeRequest(SellPreviewRequest):
    pass


class VoidTradeRequest(APIModel):
    reason: str | None = None


class TradeRead(APIModel):
    id: str
    account_id: str
    security_id: str
    market: str | None = None
    symbol: str | None = None
    name: str | None = None
    strategy_id: str
    cycle_id: str | None = None
    side: str
    executed_at: datetime
    qty: Decimal
    price: Decimal
    gross_amount: Decimal
    commission: Decimal
    stamp_duty: Decimal
    transfer_fee: Decimal
    other_fee: Decimal
    fees_total: Decimal
    source: str
    external_trade_id: str | None = None
    import_batch_id: str | None = None
    memo: str | None = None
    status: str
    client_request_id: str | None = None
    revision: int
    created_at: datetime

