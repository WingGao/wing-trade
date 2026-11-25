from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, object_session, selectinload

from app.api.deps import (
    get_current_user,
    get_db,
    request_actor,
    resolve_account,
    resolve_strategy,
    resolve_user_account_ids,
    resolve_user_lot,
    resolve_user_target,
    resolve_user_trade,
    validate_account_security,
)
from app.api.schemas.account import AccountCreate
from app.api.schemas.dashboard import DashboardRead
from app.api.schemas.security import SecurityCreate, SecurityProxyUpdate, WatchlistItemCreate
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
from app.api.v1.admin import router as admin_router
from app.api.v1.auth import router as auth_router
from app.api.v1.market import router as market_router
from app.config import settings
from app.db.session import SessionLocal
from app.domain.enums import CalcBasis, MatchingMethod, TradeSide, TradeStatus
from app.domain.exceptions import EntityNotFoundError, ValidationError
from app.domain.markets import currency_for_market
from app.domain.models import MatchRequest
from app.domain.precision import round_amount, round_price, round_qty
from app.domain.time import format_beijing_iso
from app.market_data import MarketDataHub, QuoteCollector, create_quote_provider
from app.models.auth import User
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
quote_hub = MarketDataHub(
    quote_service=quote_collector.service,
    provider=quote_collector.provider,
    session_factory=SessionLocal,
    settings=settings,
)
quote_collector.service.bind_hub(quote_hub)


def _notify_quote_hub_scope(session: Session) -> None:
    """在持仓、自选或基金代理配置变更提交后，非阻塞刷新内存行情中心订阅范围。"""
    quote_hub.refresh_scope_from_db(session=session)

router = APIRouter(prefix="/api/v1")
router.include_router(auth_router)
router.include_router(admin_router)
router.include_router(market_router)


def account_data(account: Account) -> dict[str, Any]:
    return {"id": account.id, "name": account.name, "currency": account.currency, "broker": account.broker}


def security_data(security: Security) -> dict[str, Any]:
    """序列化证券基础资料及场外基金可选参考标的配置。"""
    return {
        "id": security.id,
        "market": security.market,
        "symbol": security.symbol,
        "name": security.name,
        "securityType": security.security_type,
        "status": security.status,
        "currency": currency_for_market(security.market),
        "proxySymbol": security.proxy_symbol,
        "proxyName": security.proxy_name,
        "trackingRatio": _fmt(security.tracking_ratio, 6) if security.proxy_symbol else None,
    }


def quote_snapshot_data(
    security: Security | None,
    snapshot: Any | None,
    *,
    fallback_symbol: str | None = None,
    utc_time: bool = True,
) -> dict[str, Any]:
    """将行情快照（含盘中估算元数据）统一序列化为 API 字典。"""
    format_time = _dt_utc if utc_time else _dt
    symbol = (
        snapshot.symbol
        if snapshot is not None
        else (f"{security.market}:{security.symbol}" if security is not None else (fallback_symbol or ""))
    )
    return {
        "symbol": symbol,
        "securityId": security.id if security is not None else None,
        "market": security.market if security is not None else None,
        "currency": currency_for_market(security.market) if security is not None else None,
        "lastPrice": _fmt(snapshot.last_price, 6) if snapshot is not None else None,
        "prevClose": _fmt(snapshot.prev_close, 6) if snapshot is not None else None,
        "changePct": _fmt(snapshot.change_pct, 6) if snapshot is not None else None,
        "sourceTime": format_time(snapshot.source_time) if snapshot is not None else None,
        "fetchedAt": format_time(snapshot.fetched_at) if snapshot is not None else None,
        "provider": snapshot.provider if snapshot is not None else None,
        "isStale": True if snapshot is None else snapshot.is_stale,
        "marketSession": snapshot.market_session if snapshot is not None else None,
        "isEstimated": bool(snapshot.is_estimated) if snapshot is not None else False,
        "officialNav": _fmt(snapshot.official_nav, 6) if snapshot is not None else None,
        "officialNavDate": snapshot.official_nav_date if snapshot is not None else None,
        "proxySymbol": snapshot.proxy_symbol if snapshot is not None else None,
        "proxyName": snapshot.proxy_name if snapshot is not None else None,
        "proxyChangePct": _fmt(snapshot.proxy_change_pct, 6) if snapshot is not None else None,
        "trackingRatio": _fmt(snapshot.tracking_ratio, 6) if snapshot is not None else None,
    }


def _fmt(value: Decimal | None, scale: int = 4) -> str | None:
    return None if value is None else f"{value:.{scale}f}"


def _dt(value: datetime | None) -> str | None:
    """将数据库或领域时间统一序列化为带 +08:00 的北京时间 ISO 字符串。"""
    return format_beijing_iso(value)


def _dt_utc(value: datetime | None) -> str | None:
    """兼容旧命名，统一序列化为带 +08:00 的北京时间 ISO 字符串。"""
    return format_beijing_iso(value)


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
        "securityType": security.security_type if security is not None else None,
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
def list_accounts(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """返回当前登录用户的全部资金账户。"""
    stmt = (
        select(Account)
        .where(Account.user_id == current_user.id)
        .order_by(Account.created_at)
    )
    return [account_data(account) for account in session.scalars(stmt)]


@router.post("/accounts", status_code=status.HTTP_201_CREATED)
def create_account(
    payload: AccountCreate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """为当前登录用户创建新资金账户并初始化默认策略。"""
    begin_write(session)
    try:
        account = Account(
            user_id=current_user.id,
            name=payload.name,
            currency=payload.currency,
            broker=payload.broker,
        )
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
def list_securities(
    query: str | None = Query(default=None),
    _current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """检索公共证券资料表。"""
    stmt = select(Security).order_by(Security.market, Security.symbol)
    if query:
        term = f"%{query}%"
        stmt = stmt.where((Security.symbol.ilike(term)) | (Security.name.ilike(term)))
    return [
        security_data(s)
        for s in session.scalars(stmt)
    ]


@router.post("/securities", status_code=status.HTTP_201_CREATED)
def create_security(
    payload: SecurityCreate,
    _current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """登记公共证券基础资料，已存在时直接返回原记录。"""
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
        security_type = (
            "FUND"
            if market == "OF" and payload.security_type.upper() == "STOCK"
            else payload.security_type.upper()
        )
        security = Security(
            market=market,
            symbol=payload.symbol,
            name=payload.name,
            security_type=security_type,
        )
        session.add(security)
        session.flush()
        finish_write(session, True)
        return security_data(security)
    except Exception:
        finish_write(session, False)
        raise


@router.patch("/securities/{security_id}/proxy")
def update_security_proxy(
    security_id: str,
    payload: SecurityProxyUpdate,
    _current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """为场外基金绑定、修改或清除盘中估算跟踪参考标的（纯 A 股指数或场内 ETF）。"""
    begin_write(session)
    try:
        security = _security_or_404(session, security_id)
        if security.market.upper() != "OF":
            raise ValidationError(
                "仅场外基金（OF）支持绑定盘中跟踪参考标的",
                details={"market": security.market, "symbol": security.symbol},
            )
        raw_proxy = (payload.proxy_symbol or "").strip().upper()
        if not raw_proxy:
            security.proxy_symbol = None
            security.proxy_name = None
            security.tracking_ratio = Decimal("0.950000")
            session.flush()
            finish_write(session, True)
            _notify_quote_hub_scope(session)
            return security_data(security)

        if ":" not in raw_proxy:
            raise ValidationError(
                "参考标的代码格式无效，需要包含市场前缀（如 SH:000300 或 SZ:399006）",
                details={"proxySymbol": payload.proxy_symbol},
            )
        proxy_market, proxy_code = raw_proxy.split(":", 1)
        if proxy_market not in {"SH", "SZ", "BJ"} or len(proxy_code) != 6 or not proxy_code.isdigit():
            raise ValidationError(
                "当前仅支持绑定 A 股指数或场内 ETF（SH/SZ/BJ 6位数字代码）",
                details={"proxySymbol": payload.proxy_symbol},
            )
        ratio = payload.tracking_ratio if payload.tracking_ratio is not None else Decimal("0.950000")
        if not ratio.is_finite() or ratio <= 0 or ratio > Decimal("1.5"):
            raise ValidationError(
                "仓位跟踪系数必须在 (0, 1.5] 范围内",
                details={"trackingRatio": str(ratio)},
            )
        canonical_proxy = f"{proxy_market}:{proxy_code}"
        proxy_name = (payload.proxy_name or "").strip() or None

        # 确保参考标的在 securities 表中存在对应记录，以复用 quote_cache 共享缓存与熔断机制。
        proxy_security = session.scalar(
            select(Security).where(Security.market == proxy_market, Security.symbol == proxy_code)
        )
        if proxy_security is None:
            is_likely_index = (
                (proxy_market == "SH" and proxy_code.startswith("000"))
                or (proxy_market == "SZ" and proxy_code.startswith("399"))
            )
            proxy_security = Security(
                market=proxy_market,
                symbol=proxy_code,
                name=proxy_name,
                security_type="INDEX" if is_likely_index else "STOCK",
            )
            session.add(proxy_security)
            session.flush()
        elif proxy_name and not proxy_security.name:
            proxy_security.name = proxy_name

        security.proxy_symbol = canonical_proxy
        security.proxy_name = proxy_name or proxy_security.name
        security.tracking_ratio = round_price(ratio)
        session.flush()
        finish_write(session, True)
        _notify_quote_hub_scope(session)
        return security_data(security)
    except Exception:
        finish_write(session, False)
        raise


@router.get("/watchlist")
def list_watchlist(
    account_id: str | None = Query(default=None, alias="accountId"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """返回当前用户指定账户的自选股列表。"""
    account_id = resolve_account(session, account_id, user_id=current_user.id)
    rows = session.execute(select(WatchlistItem, Security).join(Security, Security.id == WatchlistItem.security_id).where(WatchlistItem.account_id == account_id).order_by(WatchlistItem.sort_order, WatchlistItem.created_at)).all()
    return [{"id": item.id, "accountId": item.account_id, "securityId": item.security_id, "sortOrder": item.sort_order, "security": security_data(security)} for item, security in rows]


@router.post("/watchlist", status_code=status.HTTP_201_CREATED)
def add_watchlist(
    payload: WatchlistItemCreate,
    account_id: str | None = Query(default=None, alias="accountId"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """将证券加入当前用户的自选列表。"""
    begin_write(session)
    try:
        account = resolve_account(
            session, account_id or payload.account_id, user_id=current_user.id
        )
        security = _security_or_404(session, payload.security_id)
        item = session.scalar(select(WatchlistItem).where(WatchlistItem.account_id == account, WatchlistItem.security_id == security.id))
        if item is None:
            item = WatchlistItem(account_id=account, security_id=security.id, sort_order=payload.sort_order)
            session.add(item)
            session.flush()
        finish_write(session, True)
        _notify_quote_hub_scope(session)
        return {"id": item.id, "accountId": item.account_id, "securityId": item.security_id, "sortOrder": item.sort_order}
    except Exception:
        finish_write(session, False)
        raise


@router.delete("/watchlist/{security_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_watchlist(
    security_id: str,
    account_id: str | None = Query(default=None, alias="accountId"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> None:
    """从当前用户自选列表中移除指定证券。"""
    begin_write(session)
    try:
        account = resolve_account(session, account_id, user_id=current_user.id)
        item = session.scalar(select(WatchlistItem).where(WatchlistItem.account_id == account, WatchlistItem.security_id == security_id))
        if item is not None:
            session.delete(item)
        finish_write(session, True)
        _notify_quote_hub_scope(session)
    except Exception:
        finish_write(session, False)
        raise


def _resolve_trade_scope(
    session: Session,
    user_id: str,
    account_id: str | None,
    strategy_id: str | None,
    security_id: str,
) -> tuple[str, str]:
    """解析并校验当前用户可用的交易账户、策略与证券币种匹配关系。"""
    account = resolve_account(session, account_id, user_id=user_id)
    strategy = resolve_strategy(session, account, strategy_id)
    security = _security_or_404(session, security_id)
    validate_account_security(session, account, security.market)
    return account, strategy


@router.get("/trades")
def list_trades(
    account_id: str | None = Query(default=None, alias="accountId"),
    security_id: str | None = Query(default=None, alias="securityId"),
    include_voided: bool = Query(default=False, alias="includeVoided"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """按成交时间倒序返回当前用户流水，证券名称与代码随交易一并返回。"""
    stmt = select(Trade).order_by(
        Trade.executed_at.desc(), Trade.created_at.desc(), Trade.id.desc()
    )
    if account_id:
        resolved = resolve_account(session, account_id, user_id=current_user.id)
        stmt = stmt.where(Trade.account_id == resolved)
    else:
        user_account_ids = resolve_user_account_ids(session, current_user.id)
        if not user_account_ids:
            return []
        stmt = stmt.where(Trade.account_id.in_(user_account_ids))
    if security_id:
        stmt = stmt.where(Trade.security_id == security_id)
    if not include_voided:
        stmt = stmt.where(Trade.status == TradeStatus.ACTIVE.value)
    return [trade_data(trade) for trade in session.scalars(stmt)]


@router.post("/trades/buy", status_code=status.HTTP_201_CREATED)
def record_buy(
    payload: BuyTradeRequest,
    current_user: User = Depends(get_current_user),
    actor: str = Depends(request_actor),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """为当前用户记录买入成交并生成对应批次。"""
    begin_write(session)
    try:
        account, strategy = _resolve_trade_scope(
            session, current_user.id, payload.account_id, payload.strategy_id, payload.security_id
        )
        trade = TradeLedgerService(session, actor=actor).buy(account_id=account, security_id=payload.security_id, strategy_id=strategy, executed_at=payload.executed_at, qty=payload.qty, price=payload.price, fees=fees_from(payload), client_request_id=payload.client_request_id, source=payload.source, external_trade_id=payload.external_trade_id, import_batch_id=payload.import_batch_id, memo=payload.memo, commit=False)
        finish_write(session, True)
        _notify_quote_hub_scope(session)
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
    user_id: str,
    payload: SellPreviewRequest,
) -> tuple[str, list[dict[str, Any]]]:
    """生成卖出预览批次，并附加关联买入交易的真实成交信息。"""
    account, strategy = _resolve_trade_scope(
        session, user_id, payload.account_id, payload.strategy_id, payload.security_id
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
def preview_sell(
    payload: SellPreviewRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """返回卖出金额、真实买入批次及服务端计算的分摊结果。"""
    currency, allocations = _preview_allocations(session, current_user.id, payload)
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
def record_sell(
    payload: SellTradeRequest,
    current_user: User = Depends(get_current_user),
    actor: str = Depends(request_actor),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """确认卖出并按明确批次或指定自动策略更新账务。"""
    begin_write(session)
    try:
        account, strategy = _resolve_trade_scope(
            session, current_user.id, payload.account_id, payload.strategy_id, payload.security_id
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
        _notify_quote_hub_scope(session)
        return {**trade_data(trade), "matches": [match_data(match) for match in trade.matches]}
    except Exception:
        finish_write(session, False)
        raise


@router.patch("/trades/{trade_id}")
def edit_trade(
    trade_id: str,
    payload: TradeUpdateRequest,
    current_user: User = Depends(get_current_user),
    actor: str = Depends(request_actor),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """更正原始流水并原子标记范围待刷新，不在保存时重算。"""
    begin_write(session)
    try:
        resolve_user_trade(session, trade_id, user_id=current_user.id)
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
def void_trade(
    trade_id: str,
    payload: VoidTradeRequest | None = None,
    current_user: User = Depends(get_current_user),
    actor: str = Depends(request_actor),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """撤销归属于当前用户的原始成交记录。"""
    begin_write(session)
    try:
        resolve_user_trade(session, trade_id, user_id=current_user.id)
        trade = TradeLedgerService(session, actor=actor).void_trade(trade_id, reason=payload.reason if payload else None, commit=False)
        finish_write(session, True)
        _notify_quote_hub_scope(session)
        return trade_data(trade)
    except Exception:
        finish_write(session, False)
        raise


@router.get("/positions")
def list_positions(
    account_id: str | None = Query(default=None, alias="accountId"),
    include_closed: bool = Query(default=False, alias="includeClosed"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """返回当前用户指定账户的持仓汇总列表。"""
    account_id = resolve_account(session, account_id, user_id=current_user.id)
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
        snapshots = quote_collector.service.read_cached(session, [security]) if security is not None else []
        if snapshots:
            metrics = AccountingService.position_metrics(group, latest_price=snapshots[0].last_price, realized_pnl=realized, total_buy_cash_out=buy_cash, total_sell_cash_in=sell_cash)
        result.append({"securityId": security_id, "market": security.market if security else None, "symbol": security.symbol if security else None, "name": security.name if security else None, "currency": currency_for_market(security.market) if security else None, "strategyId": strategy_id, "remainingQty": _fmt(metrics.remaining_qty), "remainingCost": _fmt(metrics.remaining_cost), "averageCost": _fmt(metrics.average_cost, 6), "realizedPnl": _fmt(metrics.realized_pnl), "unrealizedPnl": _fmt(metrics.unrealized_pnl), "strategyBreakevenPrice": _fmt(metrics.strategy_breakeven_price, 6), "fundsRecoveredSurplus": _fmt(metrics.funds_recovered_surplus)})
    return result


@router.get("/securities/{security_id}/lots")
def list_lots(
    security_id: str,
    account_id: str | None = Query(default=None, alias="accountId"),
    strategy_id: str | None = Query(default=None, alias="strategyId"),
    include_closed: bool = Query(default=False, alias="includeClosed"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """倒序返回批次估值、范围待刷新状态及可选的闭仓买卖分摊明细。"""
    account = resolve_account(session, account_id, user_id=current_user.id)
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
    last_sell = session.scalar(
        select(Trade)
        .where(
            Trade.account_id == account,
            Trade.security_id == security_id,
            Trade.strategy_id == strategy,
            Trade.side == TradeSide.SELL.value,
            Trade.status == TradeStatus.ACTIVE.value,
        )
        .order_by(Trade.executed_at.desc(), Trade.created_at.desc(), Trade.id.desc())
        .limit(1)
    )
    last_sell_price = last_sell.price if last_sell is not None else None
    vs_last_sell_pct = AccountingService.price_diff_pct(
        snapshot.last_price if snapshot is not None else None,
        last_sell_price,
    )
    summary = {
        "remainingQty": _fmt(summary_metrics.remaining_qty),
        "remainingCost": _fmt(summary_metrics.remaining_cost),
        "realizedPnl": _fmt(summary_metrics.realized_pnl),
        "unrealizedPnl": _fmt(summary_metrics.unrealized_pnl),
        "lastSellPrice": _fmt(last_sell_price, 6),
        "lastSellExecutedAt": _dt(last_sell.executed_at) if last_sell is not None else None,
        "vsLastSellPct": _fmt(vs_last_sell_pct, 6),
    }
    quote = None if snapshot is None else quote_snapshot_data(security, snapshot, utc_time=True)
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
def list_lot_matches(
    lot_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """返回归属当前用户的持仓批次有效卖出配对记录。"""
    lot = resolve_user_lot(session, lot_id, user_id=current_user.id)
    return [match_data(match) for match in lot.matches if match.sell_trade is None or match.sell_trade.status == TradeStatus.ACTIVE.value]


@router.get("/cycles")
def list_cycles(
    account_id: str | None = Query(default=None, alias="accountId"),
    security_id: str | None = Query(default=None, alias="securityId"),
    include_closed: bool = Query(default=False, alias="includeClosed"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """返回当前用户指定账户的持仓周期列表。"""
    account = resolve_account(session, account_id, user_id=current_user.id)
    stmt = select(Cycle).where(Cycle.account_id == account).order_by(Cycle.opened_at)
    if security_id:
        stmt = stmt.where(Cycle.security_id == security_id)
    if not include_closed:
        stmt = stmt.where(Cycle.status == "OPEN")
    return [{"id": c.id, "accountId": c.account_id, "securityId": c.security_id, "strategyId": c.strategy_id, "openedAt": _dt(c.opened_at), "closedAt": _dt(c.closed_at), "status": c.status} for c in session.scalars(stmt)]


@router.get("/lots/{lot_id}/targets")
def list_targets(
    lot_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """返回归属当前用户的批次显式止盈目标列表。"""
    lot = resolve_user_lot(session, lot_id, user_id=current_user.id)
    return [target_data(target) for target in lot.targets]


@router.post("/lots/{lot_id}/targets", status_code=status.HTTP_201_CREATED)
def create_target(
    lot_id: str,
    payload: ProfitTargetCreate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """新增显式分段目标，已取消或完成的目标不占用当前计划股数。"""
    begin_write(session)
    try:
        lot = resolve_user_lot(session, lot_id, user_id=current_user.id)
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
    target_id: str,
    payload: ProfitTargetPatch,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """修改显式目标并排除已取消目标的计划量。"""
    begin_write(session)
    try:
        target = resolve_user_target(session, target_id, user_id=current_user.id)
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
def delete_target(
    target_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> None:
    """取消归属当前用户的显式止盈目标。"""
    begin_write(session)
    try:
        target = resolve_user_target(session, target_id, user_id=current_user.id)
        target.status = "CANCELLED"
        session.flush()
        finish_write(session, True)
    except Exception:
        finish_write(session, False)
        raise


@router.get("/analytics/summary")
def analytics_summary(
    account_id: str | None = Query(default=None, alias="accountId"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """返回交易事实汇总及可用持仓估值。"""
    account = resolve_account(session, account_id, user_id=current_user.id)
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
    lot_security_ids = {lot.security_id for lot in lots}
    lot_securities = (
        list(session.scalars(select(Security).where(Security.id.in_(lot_security_ids))))
        if lot_security_ids
        else []
    )
    snapshots_by_symbol = {
        snapshot.symbol: snapshot
        for snapshot in quote_collector.service.read_cached(session, lot_securities)
    }
    securities_by_id = {security.id: security for security in lot_securities}
    for lot in lots:
        security = securities_by_id.get(lot.security_id)
        snapshot = (
            snapshots_by_symbol.get(quote_collector.service.security_symbol(security))
            if security is not None
            else None
        )
        if snapshot is not None:
            have_quote = True
            unrealized += snapshot.last_price * lot.remaining_qty - lot.remaining_cost
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
    user_id: str,
    security_id: str | None = None,
) -> dict[str, Any] | None:
    """在调用方写事务内保存或清除当前用户的唯一止盈覆盖值，不复制到任何批次。"""
    key = ProfitSettingsService.scope_key(security_id, user_id=user_id)
    setting = session.get(ProfitSetting, key)
    if payload.target_return_pct is None:
        if setting is not None:
            session.delete(setting)
        session.flush()
        return None
    if setting is None:
        setting = ProfitSetting(scope_key=key, user_id=user_id, security_id=security_id)
        session.add(setting)
    setting.target_return_pct = round_price(payload.target_return_pct)
    setting.calculation_basis = payload.calculation_basis
    setting.alert_enabled = payload.alert_enabled
    session.flush()
    return setting_data(setting)


@router.get("/profit-settings")
def get_global_profit_setting(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict[str, Any] | None:
    """读取当前用户的全局止盈默认值。"""
    return setting_data(
        session.get(ProfitSetting, ProfitSettingsService.scope_key(user_id=current_user.id))
    )


@router.put("/profit-settings")
def save_global_profit_setting(
    payload: ProfitSettingWrite,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict[str, Any] | None:
    """覆盖或清除当前用户全局止盈设置，读取继承该值的批次时即时生效。"""
    begin_write(session)
    try:
        result = _save_setting(session, payload, user_id=current_user.id)
        finish_write(session, True)
        _notify_quote_hub_scope(session)
        return result
    except Exception:
        finish_write(session, False)
        raise


@router.get("/securities/{security_id}/profit-settings")
def get_security_profit_setting(
    security_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict[str, Any] | None:
    """读取当前用户的个股本层止盈覆盖；null 表示使用用户全局，不写入继承副本。"""
    _security_or_404(session, security_id)
    return setting_data(
        session.get(
            ProfitSetting,
            ProfitSettingsService.scope_key(security_id, user_id=current_user.id),
        )
    )


@router.put("/securities/{security_id}/profit-settings")
def save_security_profit_setting(
    security_id: str,
    payload: ProfitSettingWrite,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict[str, Any] | None:
    """保存当前用户的个股止盈覆盖，收益率为空时恢复全局继承。"""
    begin_write(session)
    try:
        _security_or_404(session, security_id)
        result = _save_setting(
            session, payload, user_id=current_user.id, security_id=security_id
        )
        finish_write(session, True)
        _notify_quote_hub_scope(session)
        return result
    except Exception:
        finish_write(session, False)
        raise


@router.post("/securities/{security_id}/recalculate")
def recalculate_security(
    security_id: str,
    account_id: str | None = Query(default=None, alias="accountId"),
    strategy_id: str | None = Query(default=None, alias="strategyId"),
    current_user: User = Depends(get_current_user),
    actor: str = Depends(request_actor),
    session: Session = Depends(get_db),
) -> dict[str, int]:
    """原子清理并重建指定持仓派生数据，不修改交易事实或其他持仓。"""
    begin_write(session)
    try:
        account, strategy = _resolve_trade_scope(
            session, current_user.id, account_id, strategy_id, security_id
        )
        result = RecalculationService(session, actor=actor).rebuild(
            account_id=account,
            security_id=security_id,
            strategy_id=strategy,
        )
        finish_write(session, True)
        _notify_quote_hub_scope(session)
        return result
    except Exception:
        finish_write(session, False)
        raise


@router.get("/quotes")
async def list_quotes(
    symbols: str | None = Query(default=None),
    refresh: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """返回共享行情缓存快照，未指定证券代码时仅扫描当前用户的持仓与自选范围。"""
    quote_service = quote_collector.service
    if symbols:
        requested = [value.strip() for value in symbols.split(",") if value.strip()]
        security_map = quote_service.securities_for_symbols(session, requested)
        securities = list(security_map.values())
    else:
        user_account_ids = resolve_user_account_ids(session, current_user.id)
        security_map = quote_service.symbols_for_scope(session, account_ids=user_account_ids)
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
            quote_snapshot_data(
                security,
                snapshot,
                fallback_symbol=canonical,
                utc_time=False,
            )
        )
    return {"quotes": items}


def _format_sse_event(event: str, payload: dict[str, Any]) -> str:
    """将事件名与 JSON 字典序列化为标准 SSE 帧文本。"""
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {encoded}\n\n"


@router.get("/quotes/stream")
async def stream_quotes(
    request: Request,
    symbols: str | None = Query(default=None),
    account_id: str | None = Query(default=None, alias="accountId"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> StreamingResponse:
    """建立实时行情 SSE 事件流，推送首帧快照、增量报价变动与定时保活心跳。"""
    resolved_account_id = (
        resolve_account(session, account_id, user_id=current_user.id)
        if account_id is not None
        else None
    )
    extra_symbols: list[str] = []
    if symbols:
        extra_symbols = [item.strip().upper() for item in symbols.split(",") if item.strip()]
        if len(extra_symbols) > 50:
            raise ValidationError(
                "单次 SSE 临时订阅标的数不能超过 50 个",
                details={"count": len(extra_symbols)},
            )

    subscriber, snapshot_payload = quote_hub.subscribe_client(
        current_user.id,
        account_id=resolved_account_id,
        extra_symbols=extra_symbols,
        session=session,
    )

    async def _event_generator() -> AsyncIterator[str]:
        """持续输出首帧 snapshot、后续增量 quote/status 帧及 25 秒心跳，断连时回收订阅。"""
        try:
            yield _format_sse_event("snapshot", snapshot_payload)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(subscriber.queue.get(), timeout=25.0)
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                if item.get("event") == "__close__":
                    break
                yield _format_sse_event(item["event"], item["data"])
        except asyncio.CancelledError:
            raise
        finally:
            quote_hub.unsubscribe_client(subscriber.subscriber_id)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/dashboard", response_model=DashboardRead)
async def dashboard(
    account_id: str | None = Query(default=None, alias="accountId"),
    include_closed: bool = Query(default=False, alias="includeClosed"),
    mode: Literal["cache", "auto", "manual"] = Query(default="auto"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """返回账务、持仓、行情和汇总来自同一估值切片的首页数据。"""
    resolved_account_id = resolve_account(session, account_id, user_id=current_user.id)
    service = DashboardService(quote_collector)
    return await service.build(
        session,
        account_id=resolved_account_id,
        include_closed=include_closed,
        mode=mode,
    )

