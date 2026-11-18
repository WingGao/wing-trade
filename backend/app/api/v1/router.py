from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, object_session, selectinload

from app.api.deps import (
    get_db,
    request_actor,
    resolve_account,
    resolve_strategy,
    validate_account_security,
)
from app.api.schemas.account import AccountCreate
from app.api.schemas.dashboard import DashboardRead
from app.api.schemas.security import SecurityCreate, WatchlistItemCreate
from app.api.schemas.target import ProfitSettingWrite, ProfitTargetCreate, ProfitTargetPatch
from app.api.schemas.trade import (
    BuyTradeRequest,
    MatchRequestRead,
    SellPreviewRequest,
    SellTradeRequest,
    TradeUpdateRequest,
    VoidTradeRequest,
)
from app.api.utils import begin_write, fees_from, finish_write
from app.api.v1.market import router as market_router
from app.domain.enums import CalcBasis, MatchingMethod, TradeSide, TradeStatus
from app.domain.exceptions import EntityNotFoundError, ValidationError
from app.domain.markets import currency_for_market
from app.domain.models import MatchRequest
from app.domain.precision import round_amount, round_price, round_qty
from app.market_data import QuoteCollector, create_quote_provider
from app.models.ledger import (
    Account,
    Cycle,
    Lot,
    ProfitSetting,
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
from app.services.dashboard import DashboardService
from app.services.ledger import TradeLedgerService
from app.services.matching import MatchingService
from app.services.position_refresh import PositionRefreshService
from app.services.profit_settings import ProfitSettingsService
from app.services.recalculate import RecalculationService
from app.services.target import TargetService

quote_collector = QuoteCollector(create_quote_provider())

router = APIRouter(prefix="/api/v1")
router.include_router(market_router)


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


def _dt_utc(value: datetime | None) -> str | None:
    """将 SQLite 可能缺失时区的时间按 UTC 序列化。"""
    if value is None:
        return None
    normalized = (
        value.replace(tzinfo=UTC)
        if value.tzinfo is None
        else value.astimezone(UTC)
    )
    return normalized.isoformat()


def trade_data(trade: Trade) -> dict[str, Any]:
    """序列化交易及证券资料，内部主键只供追溯使用。"""
    session = object_session(trade)
    security = session.get(Security, trade.security_id) if session is not None else None
    return {
        "id": trade.id,
        "accountId": trade.account_id,
        "currency": trade.account.currency if trade.account is not None else None,
        "securityId": trade.security_id,
        "market": security.market if security is not None else None,
        "symbol": security.symbol if security is not None else None,
        "name": security.name if security is not None else None,
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


def _closed_trade_details(lots: Sequence[Lot]) -> list[dict[str, Any]]:
    """只读投影结清批次的真实买入与有效卖出，本批回款和数量使用匹配分摊。"""
    details: list[dict[str, Any]] = []
    for lot in lots:
        buy = lot.buy_trade
        scope = (lot.account_id, lot.security_id, lot.strategy_id)
        if (
            lot.status != "CLOSED"
            or buy is None
            or buy.status != TradeStatus.ACTIVE.value
            or buy.side != TradeSide.BUY.value
            or (buy.account_id, buy.security_id, buy.strategy_id) != scope
        ):
            continue
        details.append({
            "lotId": lot.id,
            "tradeId": buy.id,
            "side": "BUY",
            "executedAt": _dt_utc(buy.executed_at),
            "qty": _fmt(buy.qty),
            "originalTradeQty": _fmt(buy.qty),
            "price": _fmt(buy.price, 6),
            "cashAmount": _fmt(round_amount(buy.gross_amount + buy.fees_total)),
            "realizedPnl": None,
        })
        for match in lot.matches:
            sell = match.sell_trade
            if (
                sell is None
                or sell.status != TradeStatus.ACTIVE.value
                or sell.side != TradeSide.SELL.value
                or (sell.account_id, sell.security_id, sell.strategy_id) != scope
            ):
                continue
            details.append({
                "lotId": lot.id,
                "tradeId": sell.id,
                "side": "SELL",
                "executedAt": _dt_utc(sell.executed_at),
                "qty": _fmt(match.matched_qty),
                "originalTradeQty": _fmt(sell.qty),
                "price": _fmt(sell.price, 6),
                "cashAmount": _fmt(match.allocated_sell_net),
                "realizedPnl": _fmt(match.realized_pnl),
            })
    return sorted(
        details,
        key=lambda item: (item["executedAt"], item["tradeId"], item["lotId"], item["side"]),
        reverse=True,
    )


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
def list_trades(
    account_id: str | None = Query(default=None, alias="accountId"),
    security_id: str | None = Query(default=None, alias="securityId"),
    include_voided: bool = Query(default=False, alias="includeVoided"),
    session: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """按成交时间倒序返回流水，证券名称与代码随交易一并返回。"""
    stmt = select(Trade).order_by(
        Trade.executed_at.desc(), Trade.created_at.desc(), Trade.id.desc()
    )
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


def _requested_match(item: MatchRequestRead) -> MatchRequest:
    """将 API 批次项转换为带必填数量的服务请求。"""
    matched_qty = item.matched_qty if item.matched_qty is not None else item.qty
    if matched_qty is None:
        raise ValidationError("matched quantity is required", details={"lot_id": item.lot_id})
    return MatchRequest(item.lot_id, matched_qty)


def _preview_allocations(
    session: Session,
    payload: SellPreviewRequest,
) -> tuple[str, list[dict[str, Any]]]:
    """生成卖出预览批次，并附加关联买入交易的真实成交信息。"""
    account, strategy = _resolve_trade_scope(
        session, payload.account_id, payload.strategy_id, payload.security_id
    )
    account_record = session.get(Account, account)
    if account_record is None:
        raise EntityNotFoundError("account was not found", details={"account_id": account})
    account_currency = account_record.currency
    cycle = CycleService.get_open_cycle(session, account, payload.security_id, strategy)
    if cycle is None:
        raise ValidationError("cannot sell without an open cycle")
    cycle_lots = session.scalars(
        select(Lot).where(Lot.cycle_id == cycle.id, Lot.status == "OPEN")
    )
    lots = [lot for lot in cycle_lots if lot.remaining_qty > 0]
    lots_by_id = {lot.id: lot for lot in lots}
    calculation = AccountingService.calculate_sell(payload.qty, payload.price, fees_from(payload))
    if payload.matches is None:
        method = payload.matching_method or MatchingMethod.FIFO
        requests = MatchingService.auto_preview(
            lots,
            calculation.qty,
            method=method,
            sell_executed_at=payload.executed_at,
            account_id=account,
            security_id=payload.security_id,
            strategy_id=strategy,
        )
    else:
        requests = [_requested_match(item) for item in payload.matches]
        method = MatchingMethod.MANUAL
    copies: dict[str, object] = {
        lot.id: SimpleNamespace(
            id=lot.id,
            account_id=lot.account_id,
            security_id=lot.security_id,
            strategy_id=lot.strategy_id,
            executed_at=lot.executed_at,
            created_at=lot.created_at,
            original_qty=lot.original_qty,
            original_cost=lot.original_cost,
            remaining_qty=lot.remaining_qty,
            remaining_cost=lot.remaining_cost,
        )
        for lot in lots
    }
    allocations = MatchingService.allocate(
        copies,
        requests,
        sell_qty=calculation.qty,
        sell_cash_in=calculation.cash_in,
        method=method,
        account_id=account,
        security_id=payload.security_id,
        strategy_id=strategy,
        sell_executed_at=payload.executed_at,
    )
    matches: list[dict[str, Any]] = []
    for item in allocations:
        lot = lots_by_id[item.lot_id]
        buy_trade = lot.buy_trade
        matches.append(
            {
                "lotId": item.lot_id,
                "buyTradeId": lot.buy_trade_id,
                "buyExecutedAt": _dt_utc(buy_trade.executed_at),
                "buyPrice": _fmt(buy_trade.price, 6),
                "buyQty": _fmt(buy_trade.qty),
                "availableQty": _fmt(lot.remaining_qty),
                "matchedQty": _fmt(item.matched_qty),
                "allocatedBuyCost": _fmt(item.allocated_buy_cost),
                "allocatedSellNet": _fmt(item.allocated_sell_net),
                "realizedPnl": _fmt(item.realized_pnl),
                "matchingMethod": item.method.value,
            }
        )
    return account_currency, matches


@router.post("/trades/sell/preview")
def preview_sell(payload: SellPreviewRequest, session: Session = Depends(get_db)) -> dict[str, Any]:
    """返回卖出金额、真实买入批次及服务端计算的分摊结果。"""
    currency, allocations = _preview_allocations(session, payload)
    calculation = AccountingService.calculate_sell(payload.qty, payload.price, fees_from(payload))
    realized_pnl = sum(
        (Decimal(item["realizedPnl"]) for item in allocations),
        Decimal("0"),
    )
    return {
        "currency": currency,
        "executedAt": _dt_utc(payload.executed_at),
        "qty": _fmt(calculation.qty),
        "price": _fmt(calculation.price, 6),
        "grossAmount": _fmt(calculation.gross),
        "feesTotal": _fmt(calculation.fees.total),
        "cashIn": _fmt(calculation.cash_in),
        "matches": allocations,
        "realizedPnl": _fmt(realized_pnl),
    }


@router.post("/trades/sell", status_code=status.HTTP_201_CREATED)
def record_sell(payload: SellTradeRequest, actor: str | None = Depends(request_actor), session: Session = Depends(get_db)) -> dict[str, Any]:
    """确认卖出并按明确批次或指定自动策略更新账务。"""
    begin_write(session)
    try:
        account, strategy = _resolve_trade_scope(
            session, payload.account_id, payload.strategy_id, payload.security_id
        )
        matches = (
            None
            if payload.matches is None
            else [_requested_match(item) for item in payload.matches]
        )
        trade = TradeLedgerService(session, actor=actor).sell(
            account_id=account,
            security_id=payload.security_id,
            strategy_id=strategy,
            executed_at=payload.executed_at,
            qty=payload.qty,
            price=payload.price,
            matches=matches,
            matching_method=payload.matching_method or MatchingMethod.FIFO,
            fees=fees_from(payload),
            client_request_id=payload.client_request_id,
            source=payload.source,
            external_trade_id=payload.external_trade_id,
            import_batch_id=payload.import_batch_id,
            memo=payload.memo,
            commit=False,
        )
        finish_write(session, True)
        return {**trade_data(trade), "matches": [match_data(match) for match in trade.matches]}
    except Exception:
        finish_write(session, False)
        raise


@router.patch("/trades/{trade_id}")
def edit_trade(
    trade_id: str,
    payload: TradeUpdateRequest,
    actor: str | None = Depends(request_actor),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """更正原始流水并原子标记范围待刷新，不在保存时重算。"""
    begin_write(session)
    try:
        trade = TradeLedgerService(session, actor=actor).edit_trade(
            trade_id,
            revision=payload.revision,
            changes=payload.model_dump(exclude_unset=True, exclude={"revision", "reason"}),
            reason=payload.reason,
            commit=False,
        )
        pending = PositionRefreshService.needs_refresh(
            session, account_id=trade.account_id,
            security_id=trade.security_id, strategy_id=trade.strategy_id,
        )
        finish_write(session, True)
        return {"trade": trade_data(trade), "needsRefresh": pending}
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
            cycle_metrics = CycleService.cycle_cash_metrics(session, cycle_id)
            buy_cash += cycle_metrics["total_buy_cash_out"] or Decimal("0")
            sell_cash += cycle_metrics["total_sell_cash_in"] or Decimal("0")
        metrics = AccountingService.position_metrics(group, realized_pnl=realized, total_buy_cash_out=buy_cash, total_sell_cash_in=sell_cash)
        quote = session.get(QuoteCache, security_id)
        if quote is not None:
            metrics = AccountingService.position_metrics(group, latest_price=quote.last_price, realized_pnl=realized, total_buy_cash_out=buy_cash, total_sell_cash_in=sell_cash)
        result.append({"securityId": security_id, "market": security.market if security else None, "symbol": security.symbol if security else None, "name": security.name if security else None, "currency": currency_for_market(security.market) if security else None, "strategyId": strategy_id, "remainingQty": _fmt(metrics.remaining_qty), "remainingCost": _fmt(metrics.remaining_cost), "averageCost": _fmt(metrics.average_cost, 6), "realizedPnl": _fmt(metrics.realized_pnl), "unrealizedPnl": _fmt(metrics.unrealized_pnl), "strategyBreakevenPrice": _fmt(metrics.strategy_breakeven_price, 6), "fundsRecoveredSurplus": _fmt(metrics.funds_recovered_surplus)})
    return result


@router.get("/securities/{security_id}/lots")
def list_lots(
    security_id: str,
    account_id: str | None = Query(default=None, alias="accountId"),
    strategy_id: str | None = Query(default=None, alias="strategyId"),
    include_closed: bool = Query(default=False, alias="includeClosed"),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """倒序返回批次估值、范围待刷新状态及可选的闭仓买卖分摊明细。"""
    account = resolve_account(session, account_id)
    strategy = resolve_strategy(session, account, strategy_id)
    security = _security_or_404(session, security_id)
    stmt = (
        select(Lot)
        .where(
            Lot.account_id == account, Lot.security_id == security_id, Lot.strategy_id == strategy
        )
        .order_by(Lot.executed_at.desc(), Lot.created_at.desc(), Lot.id.desc())
    )
    if not include_closed:
        stmt = stmt.where(Lot.status == "OPEN")
    lots = list(session.scalars(stmt))
    closed_stmt = select(Lot).where(
        Lot.account_id == account,
        Lot.security_id == security_id,
        Lot.strategy_id == strategy,
        Lot.status == "CLOSED",
    )
    if include_closed:
        closed_stmt = closed_stmt.options(
            selectinload(Lot.buy_trade),
            selectinload(Lot.matches).selectinload(TradeMatch.sell_trade),
        )
    closed = list(session.scalars(closed_stmt))
    active_matches = session.scalars(
        select(TradeMatch)
        .join(Lot, Lot.id == TradeMatch.lot_id)
        .join(Trade, Trade.id == TradeMatch.sell_trade_id)
        .where(
            Lot.account_id == account,
            Lot.security_id == security_id,
            Lot.strategy_id == strategy,
            Trade.status == "ACTIVE",
        )
    )
    realized = round_amount(sum((match.realized_pnl for match in active_matches), Decimal("0")))
    snapshots = quote_collector.service.read_cached(session, [security])
    snapshot = snapshots[0] if snapshots else None
    open_lots = [
        lot for lot in lots if lot.status == "OPEN" and lot.remaining_qty > 0
    ]
    summary_metrics = AccountingService.position_metrics(
        open_lots,
        latest_price=snapshot.last_price if snapshot is not None and open_lots else None,
        realized_pnl=realized,
    )
    summary = {
        "remainingQty": _fmt(summary_metrics.remaining_qty),
        "remainingCost": _fmt(summary_metrics.remaining_cost),
        "realizedPnl": _fmt(summary_metrics.realized_pnl),
        "unrealizedPnl": _fmt(summary_metrics.unrealized_pnl),
    }
    quote = (
        None
        if snapshot is None
        else {
            "symbol": snapshot.symbol,
            "securityId": security.id,
            "market": security.market,
            "currency": currency_for_market(security.market),
            "lastPrice": _fmt(snapshot.last_price, 6),
            "prevClose": _fmt(snapshot.prev_close, 6),
            "changePct": _fmt(snapshot.change_pct, 6),
            "sourceTime": _dt_utc(snapshot.source_time),
            "fetchedAt": _dt_utc(snapshot.fetched_at),
            "provider": snapshot.provider,
            "isStale": snapshot.is_stale,
        }
    )
    rows = []
    for lot in lots:
        pnl = None
        if lot.status == "OPEN" and lot.remaining_qty > 0 and snapshot is not None:
            pnl = round_amount(snapshot.last_price * lot.remaining_qty - lot.remaining_cost)
        rows.append(
            {
                **lot_data(lot),
                "unrealizedPnl": _fmt(pnl),
                "unrealizedReturnPct": _fmt(
                    AccountingService.return_pct(pnl, lot.remaining_cost), 6
                ),
                "effectiveTarget": ProfitSettingsService.effective_target(session, lot),
            }
        )
    return {
        "lots": rows,
        "accountId": account,
        "strategyId": strategy,
        "needsRefresh": PositionRefreshService.needs_refresh(
            session, account_id=account, security_id=security_id, strategy_id=strategy
        ),
        "closedLotCount": len(closed),
        "realizedPnl": _fmt(realized),
        "summary": summary,
        "quote": quote,
        "currency": currency_for_market(security.market),
        "closedTradeDetails": _closed_trade_details(closed) if include_closed else [],
    }


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
def create_target(
    lot_id: str, payload: ProfitTargetCreate, session: Session = Depends(get_db)
) -> dict[str, Any]:
    """新增显式分段目标，已取消或完成的目标不占用当前计划股数。"""
    begin_write(session)
    try:
        lot = session.get(Lot, lot_id)
        if lot is None:
            raise EntityNotFoundError("lot was not found", details={"lot_id": lot_id})
        planned = round_qty(
            sum(
                (
                    target.planned_sell_qty
                    for target in lot.targets
                    if target.status not in {"CANCELLED", "COMPLETED"}
                ),
                Decimal("0"),
            )
            + payload.planned_sell_qty
        )
        if planned > lot.remaining_qty:
            raise ValidationError(
                "planned target quantity exceeds lot remaining quantity",
                details={"planned": str(planned), "remaining": str(lot.remaining_qty)},
            )
        price = TargetService.solve_price(
            unit_cost=lot.unit_cost,
            planned_sell_qty=payload.planned_sell_qty,
            target_return_pct=payload.target_return_pct,
            basis=CalcBasis(payload.calculation_basis),
            commission_rate=payload.commission_rate,
            minimum_commission=payload.minimum_commission,
            stamp_duty_rate=payload.stamp_duty_rate,
            transfer_fee_rate=payload.transfer_fee_rate,
        )
        target = ProfitTarget(
            lot_id=lot.id,
            target_return_pct=payload.target_return_pct,
            planned_sell_qty=payload.planned_sell_qty,
            calculation_basis=payload.calculation_basis,
            target_price_estimate=price,
            alert_enabled=payload.alert_enabled,
            status="ACTIVE",
        )
        session.add(target)
        session.flush()
        finish_write(session, True)
        return target_data(target)
    except Exception:
        finish_write(session, False)
        raise


@router.patch("/targets/{target_id}")
def update_target(
    target_id: str, payload: ProfitTargetPatch, session: Session = Depends(get_db)
) -> dict[str, Any]:
    """修改显式目标并排除已取消目标的计划量。"""
    begin_write(session)
    try:
        target = session.get(ProfitTarget, target_id)
        if target is None:
            raise EntityNotFoundError("target was not found", details={"target_id": target_id})
        lot = target.lot
        values = payload.model_dump(exclude_unset=True)
        planned = values.get("planned_sell_qty", target.planned_sell_qty)
        total = round_qty(
            sum(
                (
                    item.planned_sell_qty
                    for item in lot.targets
                    if item.id != target.id and item.status not in {"CANCELLED", "COMPLETED"}
                ),
                Decimal("0"),
            )
            + planned
        )
        if total > lot.remaining_qty:
            raise ValidationError(
                "planned target quantity exceeds lot remaining quantity",
                details={"planned": str(total), "remaining": str(lot.remaining_qty)},
            )
        for key in (
            "planned_sell_qty",
            "target_return_pct",
            "alert_enabled",
            "status",
            "calculation_basis",
        ):
            if key in values:
                setattr(target, key, values[key])
        if any(
            key in values for key in ("planned_sell_qty", "target_return_pct", "calculation_basis")
        ):
            target.target_price_estimate = TargetService.solve_price(
                unit_cost=lot.unit_cost,
                planned_sell_qty=target.planned_sell_qty,
                target_return_pct=target.target_return_pct,
                basis=CalcBasis(target.calculation_basis),
            )
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
def analytics_summary(
    account_id: str | None = Query(default=None, alias="accountId"),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """返回交易事实汇总及可用持仓估值。"""
    account = resolve_account(session, account_id)
    account_row = session.get(Account, account)
    if account_row is None:
        raise EntityNotFoundError("account was not found", details={"account_id": account})
    trades = list(
        session.scalars(
            select(Trade).where(
                Trade.account_id == account, Trade.status == TradeStatus.ACTIVE.value
            )
        )
    )
    buy_cash = round_amount(
        sum(
            (t.gross_amount + t.fees_total for t in trades if t.side == TradeSide.BUY.value),
            Decimal("0"),
        )
    )
    sell_cash = round_amount(
        sum(
            (t.gross_amount - t.fees_total for t in trades if t.side == TradeSide.SELL.value),
            Decimal("0"),
        )
    )
    realized = round_amount(
        sum(
            (m.realized_pnl for t in trades if t.side == TradeSide.SELL.value for m in t.matches),
            Decimal("0"),
        )
    )
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
    return {
        "currency": account_row.currency,
        "totalBuyCashOut": _fmt(buy_cash),
        "totalSellCashIn": _fmt(sell_cash),
        "realizedPnl": _fmt(realized),
        "unrealizedPnl": _fmt(round_amount(unrealized)) if have_quote else None,
        "remainingQty": _fmt(remaining_qty),
        "remainingCost": _fmt(remaining_cost),
    }


def setting_data(setting: ProfitSetting | None) -> dict[str, Any] | None:
    """返回本层明确设置；没有设置时保留 null 以便界面表达继承。"""
    if setting is None:
        return None
    return {
        "id": setting.scope_key,
        "securityId": setting.security_id,
        "targetReturnPct": _fmt(setting.target_return_pct, 6),
        "calculationBasis": setting.calculation_basis,
        "alertEnabled": setting.alert_enabled,
    }


def _save_setting(
    session: Session,
    payload: ProfitSettingWrite,
    *,
    security_id: str | None = None,
) -> dict[str, Any] | None:
    """在调用方写事务内保存或清除唯一覆盖值，不复制到任何批次。"""
    key = ProfitSettingsService.scope_key(security_id)
    setting = session.get(ProfitSetting, key)
    if payload.target_return_pct is None:
        if setting is not None:
            session.delete(setting)
        session.flush()
        return None
    if setting is None:
        setting = ProfitSetting(scope_key=key, security_id=security_id)
        session.add(setting)
    setting.target_return_pct = round_price(payload.target_return_pct)
    setting.calculation_basis = payload.calculation_basis
    setting.alert_enabled = payload.alert_enabled
    session.flush()
    return setting_data(setting)


@router.get("/profit-settings")
def get_global_profit_setting(session: Session = Depends(get_db)) -> dict[str, Any] | None:
    """读取全应用唯一的全局止盈默认值。"""
    return setting_data(session.get(ProfitSetting, ProfitSettingsService.scope_key()))


@router.put("/profit-settings")
def save_global_profit_setting(
    payload: ProfitSettingWrite,
    session: Session = Depends(get_db),
) -> dict[str, Any] | None:
    """覆盖或清除全局设置，读取继承该值的批次时即时生效。"""
    begin_write(session)
    try:
        result = _save_setting(session, payload)
        finish_write(session, True)
        return result
    except Exception:
        finish_write(session, False)
        raise


@router.get("/securities/{security_id}/profit-settings")
def get_security_profit_setting(
    security_id: str,
    session: Session = Depends(get_db),
) -> dict[str, Any] | None:
    """读取个股本层覆盖；null 表示使用全局，不写入继承副本。"""
    _security_or_404(session, security_id)
    return setting_data(session.get(ProfitSetting, ProfitSettingsService.scope_key(security_id)))


@router.put("/securities/{security_id}/profit-settings")
def save_security_profit_setting(
    security_id: str,
    payload: ProfitSettingWrite,
    session: Session = Depends(get_db),
) -> dict[str, Any] | None:
    """保存个股覆盖，收益率为空时恢复全局继承。"""
    begin_write(session)
    try:
        _security_or_404(session, security_id)
        result = _save_setting(session, payload, security_id=security_id)
        finish_write(session, True)
        return result
    except Exception:
        finish_write(session, False)
        raise


@router.post("/securities/{security_id}/recalculate")
def recalculate_security(
    security_id: str,
    account_id: str | None = Query(default=None, alias="accountId"),
    strategy_id: str | None = Query(default=None, alias="strategyId"),
    actor: str | None = Depends(request_actor),
    session: Session = Depends(get_db),
) -> dict[str, int]:
    """原子清理并重建指定持仓派生数据，不修改交易事实或其他持仓。"""
    begin_write(session)
    try:
        account, strategy = _resolve_trade_scope(session, account_id, strategy_id, security_id)
        result = RecalculationService(session, actor=actor).rebuild(
            account_id=account,
            security_id=security_id,
            strategy_id=strategy,
        )
        finish_write(session, True)
        return result
    except Exception:
        finish_write(session, False)
        raise


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


@router.get("/dashboard", response_model=DashboardRead)
async def dashboard(
    account_id: str | None = Query(default=None, alias="accountId"),
    include_closed: bool = Query(default=False, alias="includeClosed"),
    mode: Literal["cache", "auto", "manual"] = Query(default="auto"),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """返回账务、持仓、行情和汇总来自同一估值切片的首页数据。"""
    resolved_account_id = resolve_account(session, account_id)
    service = DashboardService(quote_collector)
    return await service.build(
        session,
        account_id=resolved_account_id,
        include_closed=include_closed,
        mode=mode,
    )

