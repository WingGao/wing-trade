from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import MatchingMethod, TradeSide, TradeSource, TradeStatus
from app.domain.exceptions import (
    LotHasActiveMatchesError,
    TradeNotFoundError,
    ValidationError,
)
from app.domain.models import Fees, MatchRequest
from app.domain.precision import round_amount, round_qty
from app.models.ledger import AuditEvent, Lot, Trade, TradeMatch
from app.services.accounting import AccountingService
from app.services.cycle import CycleService
from app.services.matching import MatchingService


class TradeLedgerService:
    """Transactional ledger operations.

    Methods flush by default and can commit when used as a standalone service.
    Callers composing a larger transaction can pass ``commit=False`` and own
    the surrounding transaction themselves.
    """

    def __init__(self, session: Session, *, actor: str | None = None) -> None:
        self.session = session
        self.actor = actor

    @staticmethod
    def _fees(fees: Fees | None, values: dict[str, object]) -> Fees:
        if fees is not None:
            return fees
        return Fees(
            commission=values.get("commission", Decimal("0")),
            stamp_duty=values.get("stamp_duty", Decimal("0")),
            transfer_fee=values.get("transfer_fee", Decimal("0")),
            other_fee=values.get("other_fee", Decimal("0")),
        )

    def _finish(self, *, commit: bool) -> None:
        self.session.flush()
        if commit:
            self.session.commit()

    def _check_duplicate(self, account_id: str, client_request_id: str | None) -> Trade | None:
        if not client_request_id:
            return None
        return self.session.scalar(
            select(Trade).where(
                Trade.account_id == account_id,
                Trade.client_request_id == client_request_id,
            )
        )

    def _audit(
        self,
        entity_type: str,
        entity_id: str,
        action: str,
        *,
        before: object | None = None,
        after: object | None = None,
        reason: str | None = None,
    ) -> None:
        def encode(value: object | None) -> str | None:
            if value is None:
                return None
            if isinstance(value, dict):
                return json.dumps(value, default=str, sort_keys=True)
            return json.dumps({"value": str(value)}, sort_keys=True)

        self.session.add(
            AuditEvent(
                entity_type=entity_type,
                entity_id=entity_id,
                action=action,
                before_json=encode(before),
                after_json=encode(after),
                actor=self.actor,
                reason=reason,
            )
        )

    @staticmethod
    def _lot_snapshot(lot: Lot) -> dict[str, str | None]:
        return {
            "id": lot.id,
            "cycle_id": lot.cycle_id,
            "status": lot.status,
            "remaining_qty": str(lot.remaining_qty),
            "remaining_cost": str(lot.remaining_cost),
        }

    @staticmethod
    def _cycle_snapshot(cycle: object) -> dict[str, str | None]:
        closed_at = getattr(cycle, "closed_at", None)
        return {
            "id": getattr(cycle, "id", None),
            "status": getattr(cycle, "status", None),
            "closed_at": closed_at.isoformat() if closed_at is not None else None,
        }

    def _audit_change(
        self,
        entity_type: str,
        entity_id: str,
        action: str,
        before: dict[str, str | None],
        after: dict[str, str | None],
    ) -> None:
        if before != after:
            self._audit(entity_type, entity_id, action, before=before, after=after)

    def buy(
        self,
        *,
        account_id: str,
        security_id: str,
        strategy_id: str,
        executed_at: datetime,
        qty: object,
        price: object,
        fees: Fees | None = None,
        client_request_id: str | None = None,
        source: TradeSource | str = TradeSource.MANUAL,
        external_trade_id: str | None = None,
        import_batch_id: str | None = None,
        memo: str | None = None,
        commit: bool = True,
        **fee_values: object,
    ) -> Trade:
        duplicate = self._check_duplicate(account_id, client_request_id)
        if duplicate is not None:
            return duplicate
        actual_fees = self._fees(fees, fee_values)
        calculation = AccountingService.calculate_buy(qty, price, actual_fees)
        existing_cycle = CycleService.get_open_cycle(
            self.session, account_id, security_id, strategy_id
        )
        cycle = CycleService.get_or_create_open_cycle(
            self.session, account_id, security_id, strategy_id, executed_at
        )
        cycle_created = existing_cycle is None
        trade = Trade(
            account_id=account_id,
            security_id=security_id,
            strategy_id=strategy_id,
            cycle_id=cycle.id,
            side=TradeSide.BUY.value,
            executed_at=executed_at,
            qty=calculation.qty,
            price=calculation.price,
            gross_amount=calculation.gross,
            commission=actual_fees.commission,
            stamp_duty=actual_fees.stamp_duty,
            transfer_fee=actual_fees.transfer_fee,
            other_fee=actual_fees.other_fee,
            fees_total=actual_fees.total,
            source=str(source),
            external_trade_id=external_trade_id,
            import_batch_id=import_batch_id,
            memo=memo,
            status=TradeStatus.ACTIVE.value,
            client_request_id=client_request_id,
            revision=1,
        )
        self.session.add(trade)
        self.session.flush()
        lot = Lot(
            buy_trade_id=trade.id,
            account_id=account_id,
            security_id=security_id,
            strategy_id=strategy_id,
            cycle_id=cycle.id,
            original_qty=calculation.qty,
            original_cost=calculation.cash_out,
            remaining_qty=calculation.qty,
            remaining_cost=calculation.cash_out,
            unit_cost=calculation.unit_cost,
            executed_at=executed_at,
            status="OPEN",
        )
        self.session.add(lot)
        self.session.flush()
        self._audit("LOT", lot.id, "LOT_CREATED", after=self._lot_snapshot(lot))
        if cycle_created:
            self._audit("CYCLE", cycle.id, "CYCLE_CREATED", after=self._cycle_snapshot(cycle))
        self._audit("TRADE", trade.id, "BUY_RECORDED", after={"qty": calculation.qty, "cash_out": calculation.cash_out})
        self._finish(commit=commit)
        return trade

    record_buy = buy

    def sell(
        self,
        *,
        account_id: str,
        security_id: str,
        strategy_id: str,
        executed_at: datetime,
        qty: object,
        price: object,
        matches: Iterable[MatchRequest | dict[str, object] | tuple[str, object]] | None = None,
        matching_method: MatchingMethod = MatchingMethod.FIFO,
        fees: Fees | None = None,
        client_request_id: str | None = None,
        source: TradeSource | str = TradeSource.MANUAL,
        external_trade_id: str | None = None,
        import_batch_id: str | None = None,
        memo: str | None = None,
        commit: bool = True,
        **fee_values: object,
    ) -> Trade:
        """按明确批次或指定自动策略记录卖出并更新批次与周期。"""
        duplicate = self._check_duplicate(account_id, client_request_id)
        if duplicate is not None:
            return duplicate
        actual_fees = self._fees(fees, fee_values)
        calculation = AccountingService.calculate_sell(qty, price, actual_fees)
        cycle = CycleService.get_open_cycle(self.session, account_id, security_id, strategy_id)
        if cycle is None:
            raise ValidationError("cannot sell without an open cycle")
        lots = list(
            self.session.scalars(
                select(Lot)
                .where(
                    Lot.cycle_id == cycle.id,
                    Lot.status == "OPEN",
                )
                .with_for_update()
            )
        )
        # ExactDecimal is stored as fixed-scale text on SQLite. Filter the
        # loaded Decimal values in Python instead of relying on SQL text
        # comparison semantics for the remaining quantity.
        lots = [lot for lot in lots if lot.remaining_qty > 0]
        lots_by_id = {lot.id: lot for lot in lots}
        lot_before = {lot.id: self._lot_snapshot(lot) for lot in lots}
        cycle_before = self._cycle_snapshot(cycle)
        if matches is None:
            method = MatchingMethod(matching_method)
            requests = MatchingService.auto_preview(
                lots,
                calculation.qty,
                method=method,
                sell_executed_at=executed_at,
                account_id=account_id,
                security_id=security_id,
                strategy_id=strategy_id,
            )
        else:
            requests = MatchingService.normalize_requests(matches)
            method = MatchingMethod.MANUAL
        allocations = MatchingService.allocate(
            lots_by_id,
            requests,
            sell_qty=calculation.qty,
            sell_cash_in=calculation.cash_in,
            method=method,
            account_id=account_id,
            security_id=security_id,
            strategy_id=strategy_id,
            sell_executed_at=executed_at,
        )
        trade = Trade(
            account_id=account_id,
            security_id=security_id,
            strategy_id=strategy_id,
            cycle_id=cycle.id,
            side=TradeSide.SELL.value,
            executed_at=executed_at,
            qty=calculation.qty,
            price=calculation.price,
            gross_amount=calculation.gross,
            commission=actual_fees.commission,
            stamp_duty=actual_fees.stamp_duty,
            transfer_fee=actual_fees.transfer_fee,
            other_fee=actual_fees.other_fee,
            fees_total=actual_fees.total,
            source=str(source),
            external_trade_id=external_trade_id,
            import_batch_id=import_batch_id,
            memo=memo,
            status=TradeStatus.ACTIVE.value,
            client_request_id=client_request_id,
            revision=1,
        )
        self.session.add(trade)
        self.session.flush()
        for allocation in allocations:
            self.session.add(
                TradeMatch(
                    sell_trade_id=trade.id,
                    lot_id=allocation.lot_id,
                    matched_qty=allocation.matched_qty,
                    allocated_buy_cost=allocation.allocated_buy_cost,
                    allocated_sell_net=allocation.allocated_sell_net,
                    realized_pnl=allocation.realized_pnl,
                    matching_method=allocation.method.value,
                )
            )
            lot = lots_by_id[allocation.lot_id]
            self._audit_change(
                "LOT",
                lot.id,
                "LOT_UPDATED",
                lot_before[lot.id],
                self._lot_snapshot(lot),
            )
        self._audit(
            "TRADE", trade.id, "SELL_RECORDED", after={"qty": calculation.qty, "cash_in": calculation.cash_in}
        )
        CycleService.refresh_cycle(self.session, cycle, at=executed_at)
        self._audit_change(
            "CYCLE",
            cycle.id,
            "CYCLE_UPDATED",
            cycle_before,
            self._cycle_snapshot(cycle),
        )
        self._finish(commit=commit)
        return trade

    record_sell = sell

    def void_trade(self, trade_id: str, *, reason: str | None = None, commit: bool = True) -> Trade:
        trade = self.session.get(Trade, trade_id)
        if trade is None:
            raise TradeNotFoundError(f"trade {trade_id} was not found")
        if trade.status == TradeStatus.VOIDED.value:
            return trade
        cycle_before = self._cycle_snapshot(trade.cycle) if trade.cycle is not None else None
        if trade.side == TradeSide.BUY.value:
            lot = trade.lot
            if lot is not None and any(
                match.sell_trade is not None and match.sell_trade.status == TradeStatus.ACTIVE.value
                for match in lot.matches
            ):
                raise LotHasActiveMatchesError("cannot void a buy trade with active matches")
            if lot is not None:
                lot_before = self._lot_snapshot(lot)
                lot.status = "CLOSED"
                lot.remaining_qty = Decimal("0.0000")
                lot.remaining_cost = Decimal("0.0000")
                self._audit_change(
                    "LOT", lot.id, "LOT_UPDATED", lot_before, self._lot_snapshot(lot)
                )
        elif trade.side == TradeSide.SELL.value:
            for match in list(trade.matches):
                lot = match.lot
                lot_before = self._lot_snapshot(lot)
                lot.remaining_qty = round_qty(lot.remaining_qty + match.matched_qty)
                lot.remaining_cost = round_amount(lot.remaining_cost + match.allocated_buy_cost)
                lot.status = "OPEN"
                self._audit_change(
                    "LOT", lot.id, "LOT_UPDATED", lot_before, self._lot_snapshot(lot)
                )
        before = {"status": trade.status}
        trade.status = TradeStatus.VOIDED.value
        trade.revision += 1
        if trade.cycle is not None:
            CycleService.refresh_cycle(self.session, trade.cycle, at=datetime.now(timezone.utc))
            self._audit_change(
                "CYCLE",
                trade.cycle.id,
                "CYCLE_UPDATED",
                cycle_before,
                self._cycle_snapshot(trade.cycle),
            )
        self._audit("TRADE", trade.id, "TRADE_VOIDED", before=before, after={"status": trade.status}, reason=reason)
        self._finish(commit=commit)
        return trade

    void = void_trade

