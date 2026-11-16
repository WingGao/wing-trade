from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, request_actor, resolve_account, resolve_strategy, validate_account_security
from app.api.schemas.account import AccountCreate
from app.api.schemas.security import SecurityCreate, WatchlistItemCreate
from app.api.schemas.target import ProfitTargetCreate, ProfitTargetPatch
from app.api.schemas.trade import BuyTradeRequest, SellPreviewRequest, SellTradeRequest, VoidTradeRequest
from app.api.utils import begin_write, fees_from, finish_write
from app.domain.enums import CalcBasis, MatchingMethod, TradeSide, TradeStatus
from app.domain.exceptions import EntityNotFoundError, ValidationError
from app.domain.models import MatchRequest
from app.domain.precision import round_amount, round_qty
from app.market_data import MockQuoteProvider, QuoteCollector
from app.models.ledger import (
    Account,
    Cycle,
    Lot,
    ProfitTarget,
    QuoteCache,
    Security,
    Strategy,
    Trade,
    TradeMatch,
    WatchlistItem,
)
from app.services.accounting import AccountingService
from app.services.cycle import CycleService
from app.services.ledger import TradeLedgerService
from app.services.matching import MatchingService
from app.services.target import TargetService
from app.domain.markets import currency_for_market


quote_collector = QuoteCollector(MockQuoteProvider())

router = APIRouter(prefix="/api/v1")


def account_data(account: Account) -> dict[str, Any]:
    return {"id": account.id, "name": account.name, "currency": account.currency, "broker": account.broker}


def security_data(security: Security) -> dict[str, Any]:
    return {
        "id": security.id,
        "market": security.market,
        "symbol": security.symbol,
        "name": security.name,
        "securityType": security.security_type,
        "status": security.status,
        "currency": currency_for_market(security.market),
    }


def _fmt(value: Decimal | None, scale: int = 4) -> str | None:
    return None if value is None else f"{value:.{scale}f}"


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def trade_data(trade: Trade) -> dict[str, Any]:
    return {
        "id": trade.id,
        "accountId": trade.account_id,
        "currency": trade.account.currency if trade.account is not None else None,
        "securityId": trade.security_id,
        "strategyId": trade.strategy_id,
        "cycleId": trade.cycle_id,
        "side": trade.side,
        "executedAt": _dt(trade.executed_at),
        "qty": _fmt(trade.qty),
        "price": _fmt(trade.price, 6),
        "grossAmount": _fmt(trade.gross_amount),
        "commission": _fmt(trade.commission),
        "stampDuty": _fmt(trade.stamp_duty),
        "transferFee": _fmt(trade.transfer_fee),
        "otherFee": _fmt(trade.other_fee),
        "feesTotal": _fmt(trade.fees_total),
        "source": trade.source,
        "externalTradeId": trade.external_trade_id,
        "importBatchId": trade.import_batch_id,
        "memo": trade.memo,
        "status": trade.status,
        "clientRequestId": trade.client_request_id,
        "revision": trade.revision,
        "createdAt": _dt(trade.created_at),
    }


def match_data(match: TradeMatch) -> dict[str, Any]:
    return {
        "id": match.id,
        "sellTradeId": match.sell_trade_id,
        "lotId": match.lot_id,
        "matchedQty": _fmt(match.matched_qty),
        "allocatedBuyCost": _fmt(match.allocated_buy_cost),
        "allocatedSellNet": _fmt(match.allocated_sell_net),
        "realizedPnl": _fmt(match.realized_pnl),
        "matchingMethod": match.matching_method,
        "createdAt": _dt(match.created_at),
    }


def target_data(target: ProfitTarget) -> dict[str, Any]:
    return {
        "id": target.id,
        "lotId": target.lot_id,
        "targetReturnPct": _fmt(target.target_return_pct, 6),
        "plannedSellQty": _fmt(target.planned_sell_qty),
        "calculationBasis": target.calculation_basis,
        "targetPriceEstimate": _fmt(target.target_price_estimate, 6),
        "alertEnabled": target.alert_enabled,
        "status": target.status,
        "triggeredAt": _dt(target.triggered_at),
        "createdAt": _dt(target.created_at),
    }


def lot_data(lot: Lot, *, include_targets: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": lot.id,
        "buyTradeId": lot.buy_trade_id,
        "accountId": lot.account_id,
        "securityId": lot.security_id,
        "strategyId": lot.strategy_id,
        "cycleId": lot.cycle_id,
        "originalQty": _fmt(lot.original_qty),
        "originalCost": _fmt(lot.original_cost),
        "remainingQty": _fmt(lot.remaining_qty),
        "remainingCost": _fmt(lot.remaining_cost),
        "unitCost": _fmt(lot.unit_cost, 6),
        "executedAt": _dt(lot.executed_at),
        "status": lot.status,
    }
    if include_targets:
        data["targets"] = [target_data(target) for target in lot.targets]
    return data


def _security_or_404(session: Session, security_id: str) -> Security:
    security = session.get(Security, security_id)
    if security is None:
        raise EntityNotFoundError("security was not found", details={"security_id": security_id})
    return security


@router.get("/accounts")
def list_accounts(session: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [account_data(account) for account in session.scalars(select(Account).order_by(Account.created_at))]


@router.post("/accounts", status_code=status.HTTP_201_CREATED)
def create_account(payload: AccountCreate, session: Session = Depends(get_db)) -> dict[str, Any]:
    begin_write(session)
    try:
        account = Account(name=payload.name, currency=payload.currency, broker=payload.broker)
        session.add(account)
        session.flush()
        session.add(Strategy(account_id=account.id, name="DEFAULT"))
        session.flush()
        finish_write(session, True)
        return account_data(account)
    except Exception:
        finish_write(session, False)
        raise


@router.get("/securities")
def list_securities(query: str | None = Query(default=None), session: Session = Depends(get_db)) -> list[dict[str, Any]]:
    stmt = select(Security).order_by(Security.market, Security.symbol)
    if query:
        term = f"%{query}%"
        stmt = stmt.where((Security.symbol.ilike(term)) | (Security.name.ilike(term)))
    return [
        security_data(s)
        for s in session.scalars(stmt)
    ]


@router.post("/securities", status_code=status.HTTP_201_CREATED)
def create_security(payload: SecurityCreate, session: Session = Depends(get_db)) -> dict[str, Any]:
    begin_write(session)
    try:
        market = payload.market.upper()
        try:
            currency_for_market(market)
        except ValueError as exc:
            raise ValidationError(str(exc), details={"market": market}) from exc
        existing = session.scalar(select(Security).where(Security.market == market, Security.symbol == payload.symbol))
        if existing is not None:
            finish_write(session, True)
            return security_data(existing)
        security = Security(market=market, symbol=payload.symbol, name=payload.name, security_type=payload.security_type)
        session.add(security)
        session.flush()
        finish_write(session, True)
        return security_data(security)
    except Exception:
        finish_write(session, False)
        raise


@router.get("/watchlist")
def list_watchlist(account_id: str | None = Query(default=None, alias="accountId"), session: Session = Depends(get_db)) -> list[dict[str, Any]]:
    account_id = resolve_account(session, account_id)
    rows = session.execute(select(WatchlistItem, Security).join(Security, Security.id == WatchlistItem.security_id).where(WatchlistItem.account_id == account_id).order_by(WatchlistItem.sort_order, WatchlistItem.created_at)).all()
    return [{"id": item.id, "accountId": item.account_id, "securityId": item.security_id, "sortOrder": item.sort_order, "security": security_data(security)} for item, security in rows]


@router.post("/watchlist", status_code=status.HTTP_201_CREATED)
def add_watchlist(payload: WatchlistItemCreate, account_id: str | None = Query(default=None, alias="accountId"), session: Session = Depends(get_db)) -> dict[str, Any]:
    begin_write(session)
    try:
        account = resolve_account(session, account_id or payload.account_id)
        security = _security_or_404(session, payload.security_id)
        item = session.scalar(select(WatchlistItem).where(WatchlistItem.account_id == account, WatchlistItem.security_id == security.id))
        if item is None:
            item = WatchlistItem(account_id=account, security_id=security.id, sort_order=payload.sort_order)
            session.add(item)
            session.flush()
        finish_write(session, True)
        return {"id": item.id, "accountId": item.account_id, "securityId": item.security_id, "sortOrder": item.sort_order}
    except Exception:
        finish_write(session, False)
        raise


@router.delete("/watchlist/{security_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_watchlist(security_id: str, account_id: str | None = Query(default=None, alias="accountId"), session: Session = Depends(get_db)) -> None:
    begin_write(session)
    try:
        account = resolve_account(session, account_id)
        item = session.scalar(select(WatchlistItem).where(WatchlistItem.account_id == account, WatchlistItem.security_id == security_id))
        if item is not None:
            session.delete(item)
        finish_write(session, True)
    except Exception:
        finish_write(session, False)
        raise


def _resolve_trade_scope(session: Session, account_id: str | None, strategy_id: str | None, security_id: str) -> tuple[str, str]:
    account = resolve_account(session, account_id)
    strategy = resolve_strategy(session, account, strategy_id)
    security = _security_or_404(session, security_id)
    validate_account_security(session, account, security.market)
    return account, strategy


@router.get("/trades")
def list_trades(account_id: str | None = Query(default=None, alias="accountId"), security_id: str | None = Query(default=None, alias="securityId"), include_voided: bool = Query(default=False, alias="includeVoided"), session: Session = Depends(get_db)) -> list[dict[str, Any]]:
    stmt = select(Trade).order_by(Trade.executed_at, Trade.created_at)
    if account_id:
        stmt = stmt.where(Trade.account_id == account_id)
    if security_id:
        stmt = stmt.where(Trade.security_id == security_id)
    if not include_voided:
        stmt = stmt.where(Trade.status == TradeStatus.ACTIVE.value)
    return [trade_data(trade) for trade in session.scalars(stmt)]


@router.post("/trades/buy", status_code=status.HTTP_201_CREATED)
def record_buy(payload: BuyTradeRequest, actor: str | None = Depends(request_actor), session: Session = Depends(get_db)) -> dict[str, Any]:
    begin_write(session)
    try:
        account, strategy = _resolve_trade_scope(session, payload.account_id, payload.strategy_id, payload.security_id)
        trade = TradeLedgerService(session, actor=actor).buy(account_id=account, security_id=payload.security_id, strategy_id=strategy, executed_at=payload.executed_at, qty=payload.qty, price=payload.price, fees=fees_from(payload), client_request_id=payload.client_request_id, source=payload.source, external_trade_id=payload.external_trade_id, import_batch_id=payload.import_batch_id, memo=payload.memo, commit=False)
        finish_write(session, True)
        return trade_data(trade)
    except Exception:
        finish_write(session, False)
        raise


def _preview_allocations(session: Session, payload: SellPreviewRequest) -> tuple[None, list[dict[str, Any]]]:
    account, strategy = _resolve_trade_scope(session, payload.account_id, payload.strategy_id, payload.security_id)
    cycle = CycleService.get_open_cycle(session, account, payload.security_id, strategy)
    if cycle is None:
        raise ValidationError("cannot sell without an open cycle")
    lots = [lot for lot in session.scalars(select(Lot).where(Lot.cycle_id == cycle.id, Lot.status == "OPEN")) if lot.remaining_qty > 0]
    calculation = AccountingService.calculate_sell(payload.qty, payload.price, fees_from(payload))
    if payload.matches is None:
        requests = MatchingService.fifo_preview(lots, calculation.qty)
        method = MatchingMethod.FIFO
    else:
        requests = [MatchRequest(item.lot_id, item.matched_qty if item.matched_qty is not None else item.qty) for item in payload.matches]
        method = MatchingMethod.MANUAL
    copies = {lot.id: SimpleNamespace(id=lot.id, account_id=lot.account_id, security_id=lot.security_id, strategy_id=lot.strategy_id, executed_at=lot.executed_at, created_at=lot.created_at, original_qty=lot.original_qty, original_cost=lot.original_cost, remaining_qty=lot.remaining_qty, remaining_cost=lot.remaining_cost) for lot in lots}
    allocations = MatchingService.allocate(copies, requests, sell_qty=calculation.qty, sell_cash_in=calculation.cash_in, method=method, account_id=account, security_id=payload.security_id, strategy_id=strategy, sell_executed_at=payload.executed_at)
    return (
        None,
        [{"lotId": item.lot_id, "matchedQty": _fmt(item.matched_qty), "allocatedBuyCost": _fmt(item.allocated_buy_cost), "allocatedSellNet": _fmt(item.allocated_sell_net), "realizedPnl": _fmt(item.realized_pnl), "matchingMethod": item.method.value} for item in allocations],
    )


@router.post("/trades/sell/preview")
def preview_sell(payload: SellPreviewRequest, session: Session = Depends(get_db)) -> dict[str, Any]:
    _, allocations = _preview_allocations(session, payload)
    calculation = AccountingService.calculate_sell(payload.qty, payload.price, fees_from(payload))
    return {"qty": _fmt(calculation.qty), "price": _fmt(calculation.price, 6), "grossAmount": _fmt(calculation.gross), "feesTotal": _fmt(calculation.fees.total), "cashIn": _fmt(calculation.cash_in), "matches": allocations, "realizedPnl": _fmt(sum((Decimal(item["realizedPnl"]) for item in allocations), Decimal("0")))}


@router.post("/trades/sell", status_code=status.HTTP_201_CREATED)
def record_sell(payload: SellTradeRequest, actor: str | None = Depends(request_actor), session: Session = Depends(get_db)) -> dict[str, Any]:
    begin_write(session)
    try:
        account, strategy = _resolve_trade_scope(session, payload.account_id, payload.strategy_id, payload.security_id)
        matches = None if payload.matches is None else [MatchRequest(item.lot_id, item.matched_qty if item.matched_qty is not None else item.qty) for item in payload.matches]
        trade = TradeLedgerService(session, actor=actor).sell(account_id=account, security_id=payload.security_id, strategy_id=strategy, executed_at=payload.executed_at, qty=payload.qty, price=payload.price, matches=matches, fees=fees_from(payload), client_request_id=payload.client_request_id, source=payload.source, external_trade_id=payload.external_trade_id, import_batch_id=payload.import_batch_id, memo=payload.memo, commit=False)
        finish_write(session, True)
        return {**trade_data(trade), "matches": [match_data(match) for match in trade.matches]}
    except Exception:
        finish_write(session, False)
        raise


@router.post("/trades/{trade_id}/void")
def void_trade(trade_id: str, payload: VoidTradeRequest | None = None, actor: str | None = Depends(request_actor), session: Session = Depends(get_db)) -> dict[str, Any]:
    begin_write(session)
    try:
        trade = TradeLedgerService(session, actor=actor).void_trade(trade_id, reason=payload.reason if payload else None, commit=False)
        finish_write(session, True)
        return trade_data(trade)
    except Exception:
        finish_write(session, False)
        raise


@router.get("/positions")
def list_positions(account_id: str | None = Query(default=None, alias="accountId"), include_closed: bool = Query(default=False, alias="includeClosed"), session: Session = Depends(get_db)) -> list[dict[str, Any]]:
    account_id = resolve_account(session, account_id)
    stmt = select(Lot).where(Lot.account_id == account_id)
    if not include_closed:
        stmt = stmt.where(Lot.status == "OPEN")
    lots = list(session.scalars(stmt))
    grouped: dict[tuple[str, str], list[Lot]] = {}
    for lot in lots:
        grouped.setdefault((lot.security_id, lot.strategy_id), []).append(lot)
    result = []
    for (security_id, strategy_id), group in grouped.items():
        security = session.get(Security, security_id)
        matches = list(
            session.scalars(
                select(TradeMatch)
                .join(Lot, Lot.id == TradeMatch.lot_id)
                .where(
                    Lot.account_id == account_id,
                    Lot.security_id == security_id,
                    Lot.strategy_id == strategy_id,
                )
            )
        )
        realized = round_amount(sum((m.realized_pnl for m in matches if m.sell_trade is None or m.sell_trade.status == TradeStatus.ACTIVE.value), Decimal("0")))
        cycle_ids = {lot.cycle_id for lot in group}
        buy_cash = Decimal("0")
        sell_cash = Decimal("0")
        for cycle_id in cycle_ids:
            metrics = CycleService.cycle_cash_metrics(session, cycle_id)
            buy_cash += metrics["total_buy_cash_out"] or Decimal("0")
            sell_cash += metrics["total_sell_cash_in"] or Decimal("0")
        metrics = AccountingService.position_metrics(group, realized_pnl=realized, total_buy_cash_out=buy_cash, total_sell_cash_in=sell_cash)
        quote = session.get(QuoteCache, security_id)
        if quote is not None:
            metrics = AccountingService.position_metrics(group, latest_price=quote.last_price, realized_pnl=realized, total_buy_cash_out=buy_cash, total_sell_cash_in=sell_cash)
        result.append({"securityId": security_id, "market": security.market if security else None, "symbol": security.symbol if security else None, "name": security.name if security else None, "currency": currency_for_market(security.market) if security else None, "strategyId": strategy_id, "remainingQty": _fmt(metrics.remaining_qty), "remainingCost": _fmt(metrics.remaining_cost), "averageCost": _fmt(metrics.average_cost, 6), "realizedPnl": _fmt(metrics.realized_pnl), "unrealizedPnl": _fmt(metrics.unrealized_pnl), "strategyBreakevenPrice": _fmt(metrics.strategy_breakeven_price, 6), "fundsRecoveredSurplus": _fmt(metrics.funds_recovered_surplus)})
    return result


@router.get("/securities/{security_id}/lots")
def list_lots(security_id: str, account_id: str | None = Query(default=None, alias="accountId"), strategy_id: str | None = Query(default=None, alias="strategyId"), include_closed: bool = Query(default=False, alias="includeClosed"), session: Session = Depends(get_db)) -> dict[str, Any]:
    account = resolve_account(session, account_id)
    strategy = resolve_strategy(session, account, strategy_id)
    stmt = select(Lot).where(Lot.account_id == account, Lot.security_id == security_id, Lot.strategy_id == strategy).order_by(Lot.executed_at, Lot.created_at)
    if not include_closed:
        stmt = stmt.where(Lot.status == "OPEN")
    lots = list(session.scalars(stmt))
    closed = list(session.scalars(select(Lot).where(Lot.account_id == account, Lot.security_id == security_id, Lot.strategy_id == strategy, Lot.status == "CLOSED")))
    realized = round_amount(sum((m.realized_pnl for lot in closed for m in lot.matches if m.sell_trade is None or m.sell_trade.status == TradeStatus.ACTIVE.value), Decimal("0")))
    return {"lots": [lot_data(lot) for lot in lots], "closedLotCount": len(closed), "realizedPnl": _fmt(realized)}


@router.get("/lots/{lot_id}/matches")
def list_lot_matches(lot_id: str, session: Session = Depends(get_db)) -> list[dict[str, Any]]:
    lot = session.get(Lot, lot_id)
    if lot is None:
        raise EntityNotFoundError("lot was not found", details={"lot_id": lot_id})
    return [match_data(match) for match in lot.matches if match.sell_trade is None or match.sell_trade.status == TradeStatus.ACTIVE.value]


@router.get("/cycles")
def list_cycles(account_id: str | None = Query(default=None, alias="accountId"), security_id: str | None = Query(default=None, alias="securityId"), include_closed: bool = Query(default=False, alias="includeClosed"), session: Session = Depends(get_db)) -> list[dict[str, Any]]:
    account = resolve_account(session, account_id)
    stmt = select(Cycle).where(Cycle.account_id == account).order_by(Cycle.opened_at)
    if security_id:
        stmt = stmt.where(Cycle.security_id == security_id)
    if not include_closed:
        stmt = stmt.where(Cycle.status == "OPEN")
    return [{"id": c.id, "accountId": c.account_id, "securityId": c.security_id, "strategyId": c.strategy_id, "openedAt": _dt(c.opened_at), "closedAt": _dt(c.closed_at), "status": c.status} for c in session.scalars(stmt)]


@router.get("/lots/{lot_id}/targets")
def list_targets(lot_id: str, session: Session = Depends(get_db)) -> list[dict[str, Any]]:
    lot = session.get(Lot, lot_id)
    if lot is None:
        raise EntityNotFoundError("lot was not found", details={"lot_id": lot_id})
    return [target_data(target) for target in lot.targets]


@router.post("/lots/{lot_id}/targets", status_code=status.HTTP_201_CREATED)
def create_target(lot_id: str, payload: ProfitTargetCreate, session: Session = Depends(get_db)) -> dict[str, Any]:
    begin_write(session)
    try:
        lot = session.get(Lot, lot_id)
        if lot is None:
            raise EntityNotFoundError("lot was not found", details={"lot_id": lot_id})
        planned = round_qty(sum((target.planned_sell_qty for target in lot.targets), Decimal("0")) + payload.planned_sell_qty)
        if planned > lot.remaining_qty:
            raise ValidationError("planned target quantity exceeds lot remaining quantity", details={"planned": str(planned), "remaining": str(lot.remaining_qty)})
        price = TargetService.solve_price(unit_cost=lot.unit_cost, planned_sell_qty=payload.planned_sell_qty, target_return_pct=payload.target_return_pct, basis=CalcBasis(payload.calculation_basis), commission_rate=payload.commission_rate, minimum_commission=payload.minimum_commission, stamp_duty_rate=payload.stamp_duty_rate, transfer_fee_rate=payload.transfer_fee_rate)
        target = ProfitTarget(lot_id=lot.id, target_return_pct=payload.target_return_pct, planned_sell_qty=payload.planned_sell_qty, calculation_basis=payload.calculation_basis, target_price_estimate=price, alert_enabled=payload.alert_enabled, status="ACTIVE")
        session.add(target)
        session.flush()
        finish_write(session, True)
        return target_data(target)
    except Exception:
        finish_write(session, False)
        raise


@router.patch("/targets/{target_id}")
def update_target(target_id: str, payload: ProfitTargetPatch, session: Session = Depends(get_db)) -> dict[str, Any]:
    begin_write(session)
    try:
        target = session.get(ProfitTarget, target_id)
        if target is None:
            raise EntityNotFoundError("target was not found", details={"target_id": target_id})
        lot = target.lot
        values = payload.model_dump(exclude_unset=True)
        planned = values.get("planned_sell_qty", target.planned_sell_qty)
        total = round_qty(sum((item.planned_sell_qty for item in lot.targets if item.id != target.id), Decimal("0")) + planned)
        if total > lot.remaining_qty:
            raise ValidationError("planned target quantity exceeds lot remaining quantity", details={"planned": str(total), "remaining": str(lot.remaining_qty)})
        for key in ("planned_sell_qty", "target_return_pct", "alert_enabled", "status", "calculation_basis"):
            if key in values:
                setattr(target, key, values[key])
        if any(key in values for key in ("planned_sell_qty", "target_return_pct", "calculation_basis")):
            target.target_price_estimate = TargetService.solve_price(unit_cost=lot.unit_cost, planned_sell_qty=target.planned_sell_qty, target_return_pct=target.target_return_pct, basis=CalcBasis(target.calculation_basis))
        session.flush()
        finish_write(session, True)
        return target_data(target)
    except Exception:
        finish_write(session, False)
        raise


@router.delete("/targets/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_target(target_id: str, session: Session = Depends(get_db)) -> None:
    begin_write(session)
    try:
        target = session.get(ProfitTarget, target_id)
        if target is None:
            raise EntityNotFoundError("target was not found", details={"target_id": target_id})
        target.status = "CANCELLED"
        session.flush()
        finish_write(session, True)
    except Exception:
        finish_write(session, False)
        raise


@router.get("/analytics/summary")
def analytics_summary(account_id: str | None = Query(default=None, alias="accountId"), session: Session = Depends(get_db)) -> dict[str, Any]:
    account = resolve_account(session, account_id)
    trades = list(session.scalars(select(Trade).where(Trade.account_id == account, Trade.status == TradeStatus.ACTIVE.value)))
    buy_cash = round_amount(sum((t.gross_amount + t.fees_total for t in trades if t.side == TradeSide.BUY.value), Decimal("0")))
    sell_cash = round_amount(sum((t.gross_amount - t.fees_total for t in trades if t.side == TradeSide.SELL.value), Decimal("0")))
    realized = round_amount(sum((m.realized_pnl for t in trades if t.side == TradeSide.SELL.value for m in t.matches), Decimal("0")))
    lots = list(session.scalars(select(Lot).where(Lot.account_id == account, Lot.status == "OPEN")))
    remaining_cost = round_amount(sum((lot.remaining_cost for lot in lots), Decimal("0")))
    remaining_qty = round_qty(sum((lot.remaining_qty for lot in lots), Decimal("0")))
    unrealized = Decimal("0")
    have_quote = False
    for lot in lots:
        quote = session.get(QuoteCache, lot.security_id)
        if quote is not None:
            have_quote = True
            unrealized += quote.last_price * lot.remaining_qty - lot.remaining_cost
    return {"currency": session.get(Account, account).currency, "totalBuyCashOut": _fmt(buy_cash), "totalSellCashIn": _fmt(sell_cash), "realizedPnl": _fmt(realized), "unrealizedPnl": _fmt(round_amount(unrealized)) if have_quote else None, "remainingQty": _fmt(remaining_qty), "remainingCost": _fmt(remaining_cost)}


@router.get("/quotes")
async def list_quotes(
    symbols: str | None = Query(default=None),
    refresh: bool = Query(default=False),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return shared cache snapshots; refreshes are serialized by QuoteCollector."""
    quote_service = quote_collector.service
    if symbols:
        requested = [value.strip() for value in symbols.split(",") if value.strip()]
        security_map = quote_service.securities_for_symbols(session, requested)
        securities = list(security_map.values())
    else:
        security_map = quote_service.symbols_for_scope(session)
        securities = list(security_map.values())
        requested = list(security_map)
    snapshots = await quote_collector.collect(session, securities, refresh=refresh)
    by_symbol = {snapshot.symbol: snapshot for snapshot in snapshots}
    items: list[dict[str, Any]] = []
    for symbol in requested:
        canonical = symbol.upper()
        security = security_map.get(canonical)
        snapshot = by_symbol.get(canonical)
        items.append(
            {
                "symbol": canonical,
                "securityId": security.id if security is not None else None,
                "market": security.market if security is not None else None,
                "currency": currency_for_market(security.market) if security is not None else None,
                "lastPrice": _fmt(snapshot.last_price, 6) if snapshot else None,
                "prevClose": _fmt(snapshot.prev_close, 6) if snapshot else None,
                "changePct": _fmt(snapshot.change_pct, 6) if snapshot else None,
                "sourceTime": _dt(snapshot.source_time) if snapshot else None,
                "fetchedAt": _dt(snapshot.fetched_at) if snapshot else None,
                "provider": snapshot.provider if snapshot else None,
                "isStale": True if snapshot is None else snapshot.is_stale,
            }
        )
    return {"quotes": items}

