from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import TradeSide, TradeStatus
from app.domain.precision import round_amount, round_qty
from app.domain.time import to_beijing
from app.market_data.collector import QuoteCollector
from app.market_data.provider import QuoteSnapshot
from app.market_data.service import QuoteService
from app.models.ledger import (
    Account,
    Lot,
    Security,
    Trade,
    TradeMatch,
    WatchlistItem,
)
from app.services.accounting import AccountingService
from app.services.cycle import CycleService
from app.services.position_refresh import PositionRefreshService

DashboardMode = Literal["cache", "auto", "manual"]


class DashboardService:
    """构建账户持仓、行情覆盖与汇总来自同一时间切片的首页读取模型。"""

    def __init__(self, collector: QuoteCollector) -> None:
        """保存共享行情采集器及其日历和缓存服务。"""
        self.collector = collector
        self.quote_service: QuoteService = collector.service
        self.calendar = collector.calendar

    async def build(
        self,
        session: Session,
        *,
        account_id: str,
        include_closed: bool = False,
        mode: DashboardMode = "auto",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """按指定模式构建含持久待刷新状态的一次首页快照。"""
        current = self._as_utc(now or datetime.now(UTC))
        account = session.get(Account, account_id)
        if account is None:
            raise ValueError(f"account was not found: {account_id}")
        account_calendar = self._calendar_for_account(account)
        market_session = account_calendar.session_phase(current)
        session_open = account_calendar.is_session_open(current)
        lots = self._load_lots(session, account_id, include_closed=include_closed)
        groups = self._group_lots(lots)
        securities = self._requested_securities(session, account_id, groups)
        snapshots = await self._load_snapshots(
            session,
            securities,
            mode=mode,
            session_open=session_open,
            now=current,
        )
        snapshots_by_symbol = {snapshot.symbol: snapshot for snapshot in snapshots}
        positions = [
            self._position_data(
                session,
                account_id=account_id,
                security_id=security_id,
                strategy_id=strategy_id,
                lots=group,
                snapshots_by_symbol=snapshots_by_symbol,
            )
            for (security_id, strategy_id), group in sorted(
                groups.items(), key=lambda item: self._group_sort_key(session, item[0])
            )
        ]
        pending_scopes = PositionRefreshService.pending_scopes(session, account_id)
        pending_keys = {(scope["securityId"], scope["strategyId"]) for scope in pending_scopes}
        for position in positions:
            key = (position["securityId"], position["strategyId"])
            position["needsRefresh"] = key in pending_keys
        summary = self._summary_data(
            session,
            account=account,
            groups=groups,
            snapshots_by_symbol=snapshots_by_symbol,
        )
        return {
            "accountId": account.id,
            "currency": account.currency,
            "asOf": to_beijing(current),
            "marketSessionOpen": session_open,
            "marketSession": market_session,
            "pollAfterSeconds": 10 if session_open else 60,
            "positions": positions,
            "pendingRefreshScopes": pending_scopes,
            "summary": summary,
            "quotes": [
                self._quote_data(security, snapshots_by_symbol.get(self._security_symbol(security)))
                for security in securities
            ],
        }

    def _calendar_for_account(self, account: Account) -> Any:
        """按账户结算币种选择对应的交易日历（USD 使用美股日历，CNY 使用 A 股日历）。"""
        if account.currency.upper() == "USD":
            return self.quote_service.us_calendar
        return self.calendar

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        """把输入时间规整为带 UTC 时区的时间。"""
        return (
            value.replace(tzinfo=UTC)
            if value.tzinfo is None
            else value.astimezone(UTC)
        )

    @staticmethod
    def _security_symbol(security: Security) -> str:
        """返回数据库证券对应的规范行情代码。"""
        return QuoteService.security_symbol(security)

    def _load_lots(self, session: Session, account_id: str, *, include_closed: bool) -> list[Lot]:
        """读取账户范围内的未结清批次或包含已结清批次的全部批次。"""
        statement = select(Lot).where(Lot.account_id == account_id)
        if not include_closed:
            statement = statement.where(Lot.status == "OPEN")
        return list(session.scalars(statement))

    @staticmethod
    def _is_active_open_lot(lot: Lot) -> bool:
        """判断批次是否为活动买入交易形成的有效剩余持仓。"""
        return (
            lot.status == "OPEN"
            and lot.remaining_qty > 0
            and lot.buy_trade is not None
            and lot.buy_trade.status == TradeStatus.ACTIVE.value
            and lot.buy_trade.side == TradeSide.BUY.value
        )

    @classmethod
    def _group_lots(cls, lots: Sequence[Lot]) -> dict[tuple[str, str], list[Lot]]:
        """按证券和策略聚合账户内的持仓批次。"""
        grouped: dict[tuple[str, str], list[Lot]] = {}
        for lot in lots:
            grouped.setdefault((lot.security_id, lot.strategy_id), []).append(lot)
        return grouped

    @staticmethod
    def _group_sort_key(session: Session, key: tuple[str, str]) -> tuple[str, str, str]:
        """为持仓行生成稳定的证券和策略排序键。"""
        security_id, strategy_id = key
        security = session.get(Security, security_id)
        if security is None:
            return ("", "", strategy_id)
        return (security.market, security.symbol, strategy_id)

    def _requested_securities(
        self,
        session: Session,
        account_id: str,
        groups: dict[tuple[str, str], list[Lot]],
    ) -> list[Security]:
        """收集当前账户持仓和自选证券的去重行情范围。"""
        security_ids = {security_id for security_id, _ in groups}
        security_ids.update(
            session.scalars(
                select(WatchlistItem.security_id).where(WatchlistItem.account_id == account_id)
            ).all()
        )
        securities = [
            security
            for security in session.scalars(
                select(Security).where(Security.id.in_(security_ids))
            ).all()
            if security is not None
        ]
        return sorted(
            securities,
            key=lambda security: (security.market, security.symbol, security.id),
        )

    def _provider_supports(self, security: Security) -> bool:
        """判断当前行情源是否支持该证券。"""
        supports = getattr(self.collector.provider, "supports_symbol", None)
        return True if supports is None else bool(supports(self._security_symbol(security)))

    async def _load_snapshots(
        self,
        session: Session,
        securities: Sequence[Security],
        *,
        mode: DashboardMode,
        session_open: bool,
        now: datetime,
    ) -> list[QuoteSnapshot]:
        """按 cache、auto 或 manual 模式读取统一行情快照。"""
        if mode not in {"cache", "auto", "manual"}:
            raise ValueError(f"unsupported dashboard mode: {mode}")
        initial = self.collector.service.read_cached(session, securities, now=now)
        if mode == "cache" or not securities:
            return initial
        refresh = mode == "manual"
        supported_symbols = [
            self._security_symbol(security)
            for security in securities
            if self._provider_supports(security)
        ]
        hub = getattr(self.collector.service, "hub", None)
        if mode == "auto" and hub is not None and hub.is_push_active():
            # 内存行情中心处于活跃推流状态且全部受支持标的均有未过期快照时，零阻塞直返。
            if hub.has_fresh_quotes(supported_symbols, now=now):
                return initial
        if mode == "auto" and session_open:
            refresh = True
        elif mode == "auto":
            snapshots_by_symbol = {snapshot.symbol: snapshot for snapshot in initial}
            has_missing = any(symbol not in snapshots_by_symbol for symbol in supported_symbols)
            has_stale = any(
                snapshots_by_symbol[symbol].is_stale
                for symbol in supported_symbols
                if symbol in snapshots_by_symbol
            )
            last_refresh_at = self.collector.last_refresh_at
            refresh_due = (
                last_refresh_at is None
                or now - last_refresh_at >= timedelta(hours=1)
            )
            refresh = has_missing or (has_stale and refresh_due)
        return await self.collector.collect(session, securities, refresh=refresh, now=now)

    def _position_data(
        self,
        session: Session,
        *,
        account_id: str,
        security_id: str,
        strategy_id: str,
        lots: Sequence[Lot],
        snapshots_by_symbol: dict[str, QuoteSnapshot],
    ) -> dict[str, Any]:
        """构建单个证券策略持仓行及其固定行情估值。"""
        security = session.get(Security, security_id)
        snapshot = (
            snapshots_by_symbol.get(self._security_symbol(security))
            if security is not None
            else None
        )
        open_lots = [lot for lot in lots if self._is_active_open_lot(lot)]
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
        realized = round_amount(
            sum(
                (
                    match.realized_pnl
                    for match in matches
                    if match.sell_trade is None
                    or match.sell_trade.status == TradeStatus.ACTIVE.value
                ),
                Decimal("0"),
            )
        )
        cycle_ids = {lot.cycle_id for lot in lots}
        buy_cash = Decimal("0")
        sell_cash = Decimal("0")
        for cycle_id in cycle_ids:
            cycle_metrics = CycleService.cycle_cash_metrics(session, cycle_id)
            buy_cash += cycle_metrics["total_buy_cash_out"] or Decimal("0")
            sell_cash += cycle_metrics["total_sell_cash_in"] or Decimal("0")
        latest_price = snapshot.last_price if snapshot is not None and open_lots else None
        metrics = AccountingService.position_metrics(
            open_lots,
            latest_price=latest_price,
            realized_pnl=realized,
            total_buy_cash_out=buy_cash,
            total_sell_cash_in=sell_cash,
        )
        lowest = AccountingService.lowest_price_group_metrics(
            open_lots,
            latest_price=latest_price,
            account_id=account_id,
            security_id=security_id,
            strategy_id=strategy_id,
        )
        return {
            "securityId": security_id,
            "market": security.market if security is not None else None,
            "symbol": security.symbol if security is not None else None,
            "name": security.name if security is not None else None,
            "securityType": security.security_type if security is not None else None,
            "currency": self._currency_for(security),
            "strategyId": strategy_id,
            "remainingQty": metrics.remaining_qty,
            "remainingCost": metrics.remaining_cost,
            "averageCost": metrics.average_cost,
            "realizedPnl": metrics.realized_pnl,
            "unrealizedPnl": metrics.unrealized_pnl,
            "unrealizedReturnPct": metrics.unrealized_return_pct,
            "strategyBreakevenPrice": metrics.strategy_breakeven_price,
            "fundsRecoveredSurplus": metrics.funds_recovered_surplus,
            "quote": self._quote_data(security, snapshot)
            if security is not None and snapshot
            else None,
            "lowestPriceGroup": (
                {
                    "purchasePrice": lowest.purchase_price,
                    "lotCount": lowest.lot_count,
                    "remainingQty": lowest.remaining_qty,
                    "remainingCost": lowest.remaining_cost,
                    "unrealizedPnl": lowest.unrealized_pnl,
                    "unrealizedReturnPct": lowest.unrealized_return_pct,
                }
                if lowest is not None
                else None
            ),
        }

    def _summary_data(
        self,
        session: Session,
        *,
        account: Account,
        groups: dict[tuple[str, str], list[Lot]],
        snapshots_by_symbol: dict[str, QuoteSnapshot],
    ) -> dict[str, Any]:
        """构建账户现金流、成本和行情覆盖状态汇总。"""
        trades = list(
            session.scalars(
                select(Trade).where(
                    Trade.account_id == account.id,
                    Trade.status == TradeStatus.ACTIVE.value,
                )
            )
        )
        buy_cash = round_amount(
            sum(
                (
                    trade.gross_amount + trade.fees_total
                    for trade in trades
                    if trade.side == TradeSide.BUY.value
                ),
                Decimal("0"),
            )
        )
        sell_cash = round_amount(
            sum(
                (
                    trade.gross_amount - trade.fees_total
                    for trade in trades
                    if trade.side == TradeSide.SELL.value
                ),
                Decimal("0"),
            )
        )
        realized = round_amount(
            sum(
                (
                    match.realized_pnl
                    for trade in trades
                    if trade.side == TradeSide.SELL.value
                    for match in trade.matches
                ),
                Decimal("0"),
            )
        )

        open_groups = {
            key: group
            for key, group in groups.items()
            if any(self._is_active_open_lot(lot) for lot in group)
        }
        remaining_qty = round_qty(
            sum(
                (
                    lot.remaining_qty
                    for group in open_groups.values()
                    for lot in group
                    if self._is_active_open_lot(lot)
                ),
                Decimal("0"),
            )
        )
        remaining_cost = round_amount(
            sum(
                (
                    lot.remaining_cost
                    for group in open_groups.values()
                    for lot in group
                    if self._is_active_open_lot(lot)
                ),
                Decimal("0"),
            )
        )
        quoted_open_positions = 0
        stale_open_positions = 0
        unrealized = Decimal("0")
        for (security_id, _), group in open_groups.items():
            security = session.get(Security, security_id)
            if security is None:
                continue
            snapshot = snapshots_by_symbol.get(self._security_symbol(security))
            if snapshot is None:
                continue
            quoted_open_positions += 1
            if snapshot.is_stale:
                stale_open_positions += 1
            open_lots = [lot for lot in group if self._is_active_open_lot(lot)]
            metrics = AccountingService.position_metrics(
                open_lots, latest_price=snapshot.last_price
            )
            unrealized += metrics.unrealized_pnl or Decimal("0")

        total_open_positions = len(open_groups)
        if total_open_positions == 0:
            status = "empty"
            summary_unrealized: Decimal | None = Decimal("0")
        elif quoted_open_positions == 0:
            status = "unavailable"
            summary_unrealized = None
        elif quoted_open_positions < total_open_positions:
            status = "partial"
            summary_unrealized = None
        elif stale_open_positions:
            status = "stale"
            summary_unrealized = round_amount(unrealized)
        else:
            status = "fresh"
            summary_unrealized = round_amount(unrealized)
        return {
            "currency": account.currency,
            "totalBuyCashOut": buy_cash,
            "totalSellCashIn": sell_cash,
            "realizedPnl": realized,
            "unrealizedPnl": summary_unrealized,
            "marketValue": (
                round_amount(remaining_cost + summary_unrealized)
                if summary_unrealized is not None
                else None
            ),
            "unrealizedReturnPct": AccountingService.return_pct(summary_unrealized, remaining_cost),
            "remainingQty": remaining_qty,
            "remainingCost": remaining_cost,
            "valuationStatus": status,
            "totalOpenPositions": total_open_positions,
            "quotedOpenPositions": quoted_open_positions,
            "staleOpenPositions": stale_open_positions,
        }

    @staticmethod
    def _currency_for(security: Security | None) -> str | None:
        """返回证券币种，缺失证券时保留空值。"""
        if security is None:
            return None
        return "USD" if security.market.upper() == "US" else "CNY"

    def _quote_data(
        self,
        security: Security,
        snapshot: QuoteSnapshot | None,
    ) -> dict[str, Any]:
        """把共享行情快照（含场外基金盘中估算元数据）转换为首页行情 DTO。"""
        return {
            "symbol": self._security_symbol(security),
            "securityId": security.id,
            "market": security.market,
            "currency": self._currency_for(security),
            "lastPrice": snapshot.last_price if snapshot is not None else None,
            "prevClose": snapshot.prev_close if snapshot is not None else None,
            "changePct": snapshot.change_pct if snapshot is not None else None,
            "sourceTime": (
                to_beijing(snapshot.source_time)
                if snapshot is not None and snapshot.source_time is not None
                else None
            ),
            "fetchedAt": (
                to_beijing(snapshot.fetched_at)
                if snapshot is not None and snapshot.fetched_at is not None
                else None
            ),
            "provider": snapshot.provider if snapshot is not None else None,
            "isStale": True if snapshot is None else snapshot.is_stale,
            "marketSession": snapshot.market_session if snapshot is not None else None,
            "isEstimated": snapshot.is_estimated if snapshot is not None else False,
            "officialNav": snapshot.official_nav if snapshot is not None else None,
            "officialNavDate": snapshot.official_nav_date if snapshot is not None else None,
            "proxySymbol": snapshot.proxy_symbol if snapshot is not None else None,
            "proxyName": snapshot.proxy_name if snapshot is not None else None,
            "proxyChangePct": snapshot.proxy_change_pct if snapshot is not None else None,
            "trackingRatio": snapshot.tracking_ratio if snapshot is not None else None,
        }
