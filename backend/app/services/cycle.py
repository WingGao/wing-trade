from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import CycleStatus, LotStatus, TradeSide, TradeStatus
from app.domain.precision import ensure_decimal, round_amount, round_price, round_qty
from app.models.ledger import Cycle, Lot, Trade


class CycleService:
    """Lifecycle and cash-flow calculations for account/security/strategy cycles."""

    @staticmethod
    def get_open_cycle(
        session: Session, account_id: str, security_id: str, strategy_id: str
    ) -> Cycle | None:
        return session.scalar(
            select(Cycle)
            .where(
                Cycle.account_id == account_id,
                Cycle.security_id == security_id,
                Cycle.strategy_id == strategy_id,
                Cycle.status == CycleStatus.OPEN.value,
            )
            .order_by(Cycle.opened_at.desc())
            .limit(1)
        )

    @classmethod
    def get_or_create_open_cycle(
        cls,
        session: Session,
        account_id: str,
        security_id: str,
        strategy_id: str,
        opened_at: datetime,
    ) -> Cycle:
        cycle = cls.get_open_cycle(session, account_id, security_id, strategy_id)
        if cycle is None:
            cycle = Cycle(
                account_id=account_id,
                security_id=security_id,
                strategy_id=strategy_id,
                opened_at=opened_at,
                status=CycleStatus.OPEN.value,
            )
            session.add(cycle)
            session.flush()
        return cycle

    @staticmethod
    def remaining_quantity(session: Session, cycle_id: str) -> Decimal:
        lots = session.scalars(
            select(Lot).where(Lot.cycle_id == cycle_id, Lot.status == LotStatus.OPEN.value)
        )
        return round_qty(sum((lot.remaining_qty for lot in lots), Decimal("0")))

    @classmethod
    def refresh_cycle(cls, session: Session, cycle: Cycle, *, at: datetime | None = None) -> Cycle:
        if cls.remaining_quantity(session, cycle.id) == 0:
            cycle.status = CycleStatus.CLOSED.value
            cycle.closed_at = at or datetime.now().astimezone()
        else:
            cycle.status = CycleStatus.OPEN.value
            cycle.closed_at = None
        session.flush()
        return cycle

    @staticmethod
    def strategy_breakeven_price(
        *, total_buy_cash_out: object, total_sell_cash_in: object, remaining_qty: object
    ) -> Decimal | None:
        quantity = round_qty(remaining_qty)
        if quantity == 0:
            return None
        unrecovered = round_amount(
            ensure_decimal(total_buy_cash_out, field="total_buy_cash_out")
            - ensure_decimal(total_sell_cash_in, field="total_sell_cash_in")
        )
        # A negative cash requirement is a recovered-funds surplus, not a
        # negative accounting cost for the remaining position.
        if unrecovered < 0:
            return None
        return round_price(unrecovered / quantity)

    @classmethod
    def cycle_cash_metrics(cls, session: Session, cycle_id: str) -> dict[str, Decimal | None]:
        trades = list(
            session.scalars(
                select(Trade).where(Trade.cycle_id == cycle_id, Trade.status == TradeStatus.ACTIVE.value)
            )
        )
        buy_cash = round_amount(sum((t.gross_amount + t.fees_total for t in trades if t.side == TradeSide.BUY.value), Decimal("0")))
        sell_cash = round_amount(sum((t.gross_amount - t.fees_total for t in trades if t.side == TradeSide.SELL.value), Decimal("0")))
        remaining = cls.remaining_quantity(session, cycle_id)
        surplus = round_amount(sell_cash - buy_cash) if sell_cash > buy_cash else None
        return {
            "total_buy_cash_out": buy_cash,
            "total_sell_cash_in": sell_cash,
            "remaining_qty": remaining,
            "strategy_breakeven_price": cls.strategy_breakeven_price(
                total_buy_cash_out=buy_cash, total_sell_cash_in=sell_cash, remaining_qty=remaining
            ),
            "funds_recovered_surplus": surplus,
        }

