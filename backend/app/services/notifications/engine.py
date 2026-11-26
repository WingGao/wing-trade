"""基于 MarketDataHub 实时行情流的止盈检测、自然日去重与聚合推送引擎。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.domain.enums import TargetStatus, TradeSide, TradeStatus
from app.domain.exceptions import NotificationDeliveryError
from app.domain.markets import currency_for_market
from app.domain.precision import round_amount, round_price
from app.domain.time import BEIJING_TZ
from app.market_data.provider import QuoteSnapshot, canonical_symbol
from app.models.ledger import (
    Account,
    Lot,
    ProfitSetting,
    ProfitTarget,
    Security,
    Trade,
    WatchlistItem,
)
from app.models.notification import BuybackAlertDelivery, NotificationChannel, ProfitAlertDelivery
from app.services.accounting import AccountingService
from app.services.profit_settings import ProfitSettingsService
from app.services.target import TargetService

from .channels import WECOM_CHANNEL_TYPE, PushChannel, WeComWebhookChannel
from .formatter import (
    BuybackAlertItem,
    TakeProfitAlertItem,
    build_buyback_markdown_batches,
    build_take_profit_markdown_batches,
)

if TYPE_CHECKING:
    from app.market_data.hub import MarketDataHub

logger = logging.getLogger("ashare_ledger.notifications.engine")


def _now_utc() -> datetime:
    """返回当前 UTC 时间。"""
    return datetime.now(UTC)


def build_target_signature(
    *,
    source: str,
    basis: str,
    target_return_pct: Decimal,
    target_price: Decimal,
    planned_qty: Decimal,
) -> str:
    """构造稳定目标指纹，当目标收益率、目标价、口径或计划量变化时自动刷新。"""
    return (
        f"{source.upper()}|{basis.upper()}|"
        f"{target_return_pct:.6f}|{target_price:.6f}|{planned_qty:.4f}"
    )


def build_buyback_signature(target_price: Decimal) -> str:
    """构造目标买回价稳定指纹，当目标买回价修改时自动允许重新触发提醒。"""
    return f"BUYBACK|{target_price:.6f}"


@dataclass(frozen=True, slots=True)
class _DispatchJob:
    """单个用户某个启用渠道在本轮需要发送的止盈命中集合。"""

    user_id: str
    channel_type: str
    target_url: str
    items: list[TakeProfitAlertItem]


@dataclass(frozen=True, slots=True)
class _BuybackDispatchJob:
    """单个用户某个启用渠道在本轮需要发送的目标买回价命中集合。"""

    user_id: str
    channel_type: str
    target_url: str
    items: list[BuybackAlertItem]


@dataclass(frozen=True, slots=True)
class _DeliveredRecord:
    """单笔已确认外部发送成功的止盈提醒记录。"""

    item: TakeProfitAlertItem
    channel_type: str
    trigger_date: str


@dataclass(frozen=True, slots=True)
class _DeliveredBuybackRecord:
    """单条已确认外部发送成功的买回价提醒记录。"""

    item: BuybackAlertItem
    channel_type: str
    trigger_date: str


class ProfitAlertEngine:
    """后台异步止盈评估与推送引擎：非阻塞监听行情变化、按自然日去重并聚合推送。"""

    def __init__(
        self,
        *,
        quote_hub: MarketDataHub,
        session_factory: Callable[[], Session],
        channels: Mapping[str, PushChannel] | None = None,
        debounce_seconds: float = 0.5,
        max_retries: int = 3,
        retry_backoff_seconds: Sequence[float] = (1.0, 3.0),
        failure_cooldown_seconds: float = 60.0,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        """初始化止盈提醒引擎配置、推送通道注册表与内存合并队列。"""
        self._quote_hub = quote_hub
        self._session_factory = session_factory
        self._channels: dict[str, PushChannel] = dict(
            channels if channels is not None else {WECOM_CHANNEL_TYPE: WeComWebhookChannel()}
        )
        self._debounce_seconds = max(0.0, float(debounce_seconds))
        self._max_retries = max(1, int(max_retries))
        self._retry_backoff_seconds = tuple(max(0.0, float(v)) for v in retry_backoff_seconds)
        self._failure_cooldown_seconds = max(0.0, float(failure_cooldown_seconds))
        self._now = now or _now_utc

        self._pending_symbols: set[str] = set()
        self._wakeup_event: asyncio.Event | None = None
        self._worker_task: asyncio.Task[Any] | None = None
        self._running = False
        self._failure_cooldown_until: dict[tuple[str, str], datetime] = {}

    def get_channel(self, channel_type: str = WECOM_CHANNEL_TYPE) -> PushChannel | None:
        """按渠道标识获取已注册的推送通道实现。"""
        return self._channels.get(channel_type)

    async def start(self) -> None:
        """启动后台止盈评估协程并向 MarketDataHub 注册行情监听。"""
        if self._running:
            return
        self._running = True
        self._wakeup_event = asyncio.Event()
        self._quote_hub.add_quote_listener(self.notify_quotes_updated)
        self._worker_task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """停止后台止盈评估协程并注销行情监听。"""
        self._running = False
        self._quote_hub.remove_quote_listener(self.notify_quotes_updated)
        if self._wakeup_event is not None:
            self._wakeup_event.set()
        if self._worker_task is not None:
            self._worker_task.cancel()
            await asyncio.gather(self._worker_task, return_exceptions=True)
            self._worker_task = None
        self._pending_symbols.clear()

    def notify_quotes_updated(self, symbols: Sequence[str]) -> None:
        """非阻塞接收行情中心发布的变更标的，仅入队并唤醒后台评估协程。"""
        if not self._running or not symbols:
            return
        for symbol in symbols:
            if symbol:
                self._pending_symbols.add(canonical_symbol(symbol))
        if self._wakeup_event is not None and self._pending_symbols:
            self._wakeup_event.set()

    async def _run_loop(self) -> None:
        """后台合并防抖循环：将高频行情变更为短周期批量评估。"""
        while self._running:
            if self._wakeup_event is None:
                return
            await self._wakeup_event.wait()
            if not self._running:
                break
            if self._debounce_seconds > 0:
                await asyncio.sleep(self._debounce_seconds)
            if not self._running:
                break
            self._wakeup_event.clear()
            batch = sorted(self._pending_symbols)
            self._pending_symbols.clear()
            if not batch:
                continue
            try:
                await self.evaluate_and_dispatch(batch)
            except Exception as exc:
                logger.warning("后台止盈评估执行异常: %s", exc)

    def _is_triggerable_snapshot(
        self,
        symbol: str,
        snapshot: QuoteSnapshot | None,
        *,
        now: datetime,
    ) -> bool:
        """按市场日历与净值日期规则判断该行情快照是否允许触发止盈推送。"""
        if snapshot is None or snapshot.is_stale or snapshot.last_price <= 0:
            return False
        market = symbol.split(":", 1)[0].upper() if ":" in symbol else "SH"
        if market == "OF":
            if snapshot.is_estimated:
                return snapshot.market_session not in (None, "closed")
            if snapshot.source_time is None:
                return False
            today_bj = now.astimezone(BEIJING_TZ).date()
            return snapshot.source_time.astimezone(BEIJING_TZ).date() == today_bj
        if market == "US":
            return self._quote_hub.quote_service.us_calendar.is_session_open(now)
        return self._quote_hub.quote_service.calendar.is_session_open(now)

    def _in_failure_cooldown(self, user_id: str, channel_type: str, now: datetime) -> bool:
        """判断某用户某渠道是否正处于失败退避冷却期内。"""
        until = self._failure_cooldown_until.get((user_id, channel_type))
        if until is None:
            return False
        if now >= until:
            del self._failure_cooldown_until[(user_id, channel_type)]
            return False
        return True

    def _collect_dispatch_jobs(
        self,
        triggerable: Mapping[str, QuoteSnapshot],
        now: datetime,
    ) -> tuple[str, list[_DispatchJob]]:
        """在短读事务内评估所有启用推送用户的三层止盈目标并生成去重后的待发任务。"""
        trigger_date = now.astimezone(BEIJING_TZ).date().isoformat()
        with self._session_factory() as session:
            channels = list(
                session.scalars(
                    select(NotificationChannel).where(NotificationChannel.enabled.is_(True))
                ).all()
            )
            active_channels: dict[str, list[NotificationChannel]] = {}
            for ch in channels:
                if not ch.webhook_url or ch.channel_type not in self._channels:
                    continue
                if self._in_failure_cooldown(ch.user_id, ch.channel_type, now):
                    continue
                active_channels.setdefault(ch.user_id, []).append(ch)
            if not active_channels:
                return trigger_date, []

            enabled_user_ids = set(active_channels.keys())
            securities_by_symbol = self._quote_hub.quote_service.securities_for_symbols(
                session, list(triggerable.keys())
            )
            if not securities_by_symbol:
                return trigger_date, []
            securities_by_id = {sec.id: sec for sec in securities_by_symbol.values()}

            accounts = list(
                session.scalars(
                    select(Account).where(Account.user_id.in_(enabled_user_ids))
                ).all()
            )
            if not accounts:
                return trigger_date, []
            accounts_by_id = {acc.id: acc for acc in accounts}

            lots = list(
                session.scalars(
                    select(Lot)
                    .options(selectinload(Lot.targets))
                    .where(
                        Lot.status == "OPEN",
                        Lot.security_id.in_(securities_by_id.keys()),
                        Lot.account_id.in_(accounts_by_id.keys()),
                    )
                ).all()
            )
            # SQLite 中 ExactDecimal 以文本存储，数量过滤必须在 Python 层进行。
            open_lots = [lot for lot in lots if lot.remaining_qty > 0]
            if not open_lots:
                return trigger_date, []

            settings_rows = list(
                session.scalars(
                    select(ProfitSetting).where(ProfitSetting.user_id.in_(enabled_user_ids))
                ).all()
            )
            settings_by_key = {row.scope_key: row for row in settings_rows}

            delivered_rows = list(
                session.scalars(
                    select(ProfitAlertDelivery).where(
                        ProfitAlertDelivery.user_id.in_(enabled_user_ids),
                        ProfitAlertDelivery.trigger_date == trigger_date,
                    )
                ).all()
            )
            delivered_keys = {
                (
                    row.user_id,
                    row.lot_id,
                    row.target_key,
                    row.target_signature,
                    row.channel_type,
                )
                for row in delivered_rows
            }

            grouped_items: dict[tuple[str, str, str], list[TakeProfitAlertItem]] = {}
            for lot in sorted(open_lots, key=lambda item: (item.executed_at, item.id)):
                account = accounts_by_id.get(lot.account_id)
                security = securities_by_id.get(lot.security_id)
                if account is None or security is None or not account.user_id:
                    continue
                canonical = self._quote_hub.quote_service.security_symbol(security)
                snapshot = triggerable.get(canonical)
                if snapshot is None:
                    continue
                user_channels = active_channels.get(account.user_id, ())
                if not user_channels:
                    continue

                matched_targets = self._evaluate_lot_targets(
                    lot=lot,
                    account=account,
                    security=security,
                    canonical=canonical,
                    snapshot=snapshot,
                    settings_by_key=settings_by_key,
                )
                if not matched_targets:
                    continue

                for ch in user_channels:
                    assert ch.webhook_url is not None
                    bucket_key = (account.user_id, ch.channel_type, ch.webhook_url)
                    for alert_item in matched_targets:
                        dedup_key = (
                            account.user_id,
                            alert_item.lot_id,
                            alert_item.target_key,
                            alert_item.target_signature,
                            ch.channel_type,
                        )
                        if dedup_key in delivered_keys:
                            continue
                        grouped_items.setdefault(bucket_key, []).append(alert_item)

            jobs = [
                _DispatchJob(
                    user_id=user_id,
                    channel_type=channel_type,
                    target_url=target_url,
                    items=items,
                )
                for (user_id, channel_type, target_url), items in grouped_items.items()
                if items
            ]
            return trigger_date, jobs

    def _collect_buyback_dispatch_jobs(
        self,
        triggerable: Mapping[str, QuoteSnapshot],
        now: datetime,
    ) -> tuple[str, list[_BuybackDispatchJob]]:
        """在短读事务内评估所有启用推送用户的关注买回价目标并生成去重后的待发任务。"""
        trigger_date = now.astimezone(BEIJING_TZ).date().isoformat()
        with self._session_factory() as session:
            channels = list(
                session.scalars(
                    select(NotificationChannel).where(NotificationChannel.enabled.is_(True))
                ).all()
            )
            active_channels: dict[str, list[NotificationChannel]] = {}
            for ch in channels:
                if not ch.webhook_url or ch.channel_type not in self._channels:
                    continue
                if self._in_failure_cooldown(ch.user_id, ch.channel_type, now):
                    continue
                active_channels.setdefault(ch.user_id, []).append(ch)
            if not active_channels:
                return trigger_date, []

            enabled_user_ids = set(active_channels.keys())
            securities_by_symbol = self._quote_hub.quote_service.securities_for_symbols(
                session, list(triggerable.keys())
            )
            if not securities_by_symbol:
                return trigger_date, []
            securities_by_id = {sec.id: sec for sec in securities_by_symbol.values()}

            accounts = list(
                session.scalars(
                    select(Account).where(Account.user_id.in_(enabled_user_ids))
                ).all()
            )
            if not accounts:
                return trigger_date, []
            accounts_by_id = {acc.id: acc for acc in accounts}

            watch_items = list(
                session.scalars(
                    select(WatchlistItem).where(
                        WatchlistItem.alert_enabled.is_(True),
                        WatchlistItem.target_buy_price.is_not(None),
                        WatchlistItem.security_id.in_(securities_by_id.keys()),
                        WatchlistItem.account_id.in_(accounts_by_id.keys()),
                    )
                ).all()
            )
            eligible_items = [
                item
                for item in watch_items
                if item.target_buy_price is not None and item.target_buy_price > 0
            ]
            if not eligible_items:
                return trigger_date, []

            # 统计各 (account_id, security_id) 是否有未结清仓位，用于消息卡片状态展示（不阻塞触发）
            watched_account_ids = {item.account_id for item in eligible_items}
            watched_sec_ids = {item.security_id for item in eligible_items}
            open_lots = list(
                session.scalars(
                    select(Lot).where(
                        Lot.status == "OPEN",
                        Lot.account_id.in_(watched_account_ids),
                        Lot.security_id.in_(watched_sec_ids),
                    )
                ).all()
            )
            open_scopes = {
                (lot.account_id, lot.security_id)
                for lot in open_lots
                if lot.remaining_qty > 0
            }

            sell_trades = list(
                session.scalars(
                    select(Trade)
                    .where(
                        Trade.account_id.in_(watched_account_ids),
                        Trade.security_id.in_(watched_sec_ids),
                        Trade.side == TradeSide.SELL.value,
                        Trade.status == TradeStatus.ACTIVE.value,
                    )
                    .order_by(Trade.executed_at.desc(), Trade.created_at.desc(), Trade.id.desc())
                ).all()
            )
            latest_sell_by_scope: dict[tuple[str, str], Trade] = {}
            for trade in sell_trades:
                scope = (trade.account_id, trade.security_id)
                if scope not in latest_sell_by_scope:
                    latest_sell_by_scope[scope] = trade

            delivered_rows = list(
                session.scalars(
                    select(BuybackAlertDelivery).where(
                        BuybackAlertDelivery.user_id.in_(enabled_user_ids),
                        BuybackAlertDelivery.trigger_date == trigger_date,
                    )
                ).all()
            )
            delivered_keys = {
                (
                    row.user_id,
                    row.watchlist_item_id,
                    row.target_signature,
                    row.channel_type,
                )
                for row in delivered_rows
            }

            grouped_items: dict[tuple[str, str, str], list[BuybackAlertItem]] = {}
            sorted_eligible = sorted(
                eligible_items, key=lambda row: (row.sort_order, row.created_at, row.id)
            )
            for item in sorted_eligible:
                account = accounts_by_id.get(item.account_id)
                security = securities_by_id.get(item.security_id)
                if account is None or security is None or not account.user_id:
                    continue
                canonical = self._quote_hub.quote_service.security_symbol(security)
                snapshot = triggerable.get(canonical)
                if snapshot is None or item.target_buy_price is None:
                    continue
                if snapshot.last_price > item.target_buy_price:
                    continue
                user_channels = active_channels.get(account.user_id, ())
                if not user_channels:
                    continue

                signature = build_buyback_signature(item.target_buy_price)
                last_sell = latest_sell_by_scope.get((account.id, security.id))
                last_sell_price = last_sell.price if last_sell is not None else None
                sec_name = (security.name or "").strip() or security.symbol
                alert_item = BuybackAlertItem(
                    user_id=account.user_id,
                    account_id=account.id,
                    account_name=account.name,
                    security_id=security.id,
                    watchlist_item_id=item.id,
                    security_name=sec_name,
                    canonical_symbol=canonical,
                    market=security.market.upper(),
                    currency=currency_for_market(security.market),
                    target_signature=signature,
                    last_price=snapshot.last_price,
                    target_price=item.target_buy_price,
                    vs_target_pct=AccountingService.price_diff_pct(
                        snapshot.last_price, item.target_buy_price
                    ),
                    last_sell_price=last_sell_price,
                    vs_last_sell_pct=AccountingService.price_diff_pct(
                        snapshot.last_price, last_sell_price
                    ),
                    has_open_position=(account.id, security.id) in open_scopes,
                    note=item.note,
                    is_estimated=bool(snapshot.is_estimated),
                )
                for ch in user_channels:
                    assert ch.webhook_url is not None
                    dedup_key = (
                        account.user_id,
                        item.id,
                        signature,
                        ch.channel_type,
                    )
                    if dedup_key in delivered_keys:
                        continue
                    bucket_key = (account.user_id, ch.channel_type, ch.webhook_url)
                    grouped_items.setdefault(bucket_key, []).append(alert_item)

            jobs = [
                _BuybackDispatchJob(
                    user_id=user_id,
                    channel_type=channel_type,
                    target_url=target_url,
                    items=items,
                )
                for (user_id, channel_type, target_url), items in grouped_items.items()
                if items
            ]
            return trigger_date, jobs

    @staticmethod
    def _evaluate_lot_targets(
        *,
        lot: Lot,
        account: Account,
        security: Security,
        canonical: str,
        snapshot: QuoteSnapshot,
        settings_by_key: Mapping[str, ProfitSetting],
    ) -> list[TakeProfitAlertItem]:
        """按 LOT > SECURITY > GLOBAL 三层继承优先级评估单批次命中的所有止盈项。"""
        assert account.user_id is not None
        explicit = [
            target
            for target in lot.targets
            if target.status in {TargetStatus.ACTIVE.value, TargetStatus.TRIGGERED.value}
            and target.planned_sell_qty > 0
        ]
        results: list[TakeProfitAlertItem] = []
        sec_name = (security.name or "").strip() or security.symbol
        currency = currency_for_market(security.market)
        current_return_pct = (
            round_price((snapshot.last_price - lot.unit_cost) / lot.unit_cost)
            if lot.unit_cost > 0
            else None
        )

        if explicit:
            for target in sorted(explicit, key=lambda item: (item.created_at, item.id)):
                if not target.alert_enabled or target.target_price_estimate is None:
                    continue
                if snapshot.last_price < target.target_price_estimate:
                    continue
                signature = build_target_signature(
                    source="LOT",
                    basis=target.calculation_basis,
                    target_return_pct=target.target_return_pct,
                    target_price=target.target_price_estimate,
                    planned_qty=target.planned_sell_qty,
                )
                estimated_pnl = round_amount(
                    (snapshot.last_price - lot.unit_cost) * target.planned_sell_qty
                )
                results.append(
                    TakeProfitAlertItem(
                        user_id=account.user_id,
                        account_name=account.name,
                        security_name=sec_name,
                        canonical_symbol=canonical,
                        market=security.market.upper(),
                        currency=currency,
                        lot_id=lot.id,
                        explicit_target_id=target.id,
                        target_key=f"LOT:{target.id}",
                        target_signature=signature,
                        target_source="LOT",
                        calculation_basis=target.calculation_basis,
                        unit_cost=lot.unit_cost,
                        planned_qty=target.planned_sell_qty,
                        last_price=snapshot.last_price,
                        target_price=target.target_price_estimate,
                        current_return_pct=current_return_pct,
                        target_return_pct=target.target_return_pct,
                        estimated_pnl=estimated_pnl,
                        is_estimated=bool(snapshot.is_estimated),
                    )
                )
            return results

        sec_key = ProfitSettingsService.scope_key(lot.security_id, user_id=account.user_id)
        setting = settings_by_key.get(sec_key)
        source = "SECURITY"
        if setting is None:
            global_key = ProfitSettingsService.scope_key(user_id=account.user_id)
            setting = settings_by_key.get(global_key)
            source = "GLOBAL"
        if setting is None or not setting.alert_enabled:
            return []

        target_price = TargetService.solve_price(
            unit_cost=lot.unit_cost,
            planned_sell_qty=lot.remaining_qty,
            target_return_pct=setting.target_return_pct,
            basis=setting.calculation_basis,
        )
        if snapshot.last_price < target_price:
            return []

        signature = build_target_signature(
            source=source,
            basis=setting.calculation_basis,
            target_return_pct=setting.target_return_pct,
            target_price=target_price,
            planned_qty=lot.remaining_qty,
        )
        estimated_pnl = round_amount((snapshot.last_price - lot.unit_cost) * lot.remaining_qty)
        results.append(
            TakeProfitAlertItem(
                user_id=account.user_id,
                account_name=account.name,
                security_name=sec_name,
                canonical_symbol=canonical,
                market=security.market.upper(),
                currency=currency,
                lot_id=lot.id,
                explicit_target_id=None,
                target_key="INHERITED",
                target_signature=signature,
                target_source=source,
                calculation_basis=setting.calculation_basis,
                unit_cost=lot.unit_cost,
                planned_qty=lot.remaining_qty,
                last_price=snapshot.last_price,
                target_price=target_price,
                current_return_pct=current_return_pct,
                target_return_pct=setting.target_return_pct,
                estimated_pnl=estimated_pnl,
                is_estimated=bool(snapshot.is_estimated),
            )
        )
        return results

    async def _send_with_retry(
        self,
        channel: PushChannel,
        target_url: str,
        content: str,
    ) -> bool:
        """在异步线程中执行外部推送，并按有限退避次数重试可重试错误。"""
        for attempt in range(self._max_retries):
            try:
                await asyncio.to_thread(channel.send_markdown, target_url, content)
                return True
            except NotificationDeliveryError as exc:
                retryable = bool(exc.details.get("retryable", True))
                if not retryable or attempt + 1 >= self._max_retries:
                    logger.warning(
                        "止盈推送最终失败（attempt=%d/%d retryable=%s）: %s",
                        attempt + 1,
                        self._max_retries,
                        retryable,
                        exc.message,
                    )
                    return False
                delay = (
                    self._retry_backoff_seconds[attempt]
                    if attempt < len(self._retry_backoff_seconds)
                    else self._retry_backoff_seconds[-1]
                    if self._retry_backoff_seconds
                    else 0.0
                )
                if delay > 0:
                    await asyncio.sleep(delay)
            except Exception as exc:
                logger.warning("止盈推送出现未预期异常: %s", type(exc).__name__)
                return False
        return False

    def _record_deliveries(
        self,
        delivered: Sequence[_DeliveredRecord],
        now: datetime,
    ) -> int:
        """在短写事务中幂等持久化成功送达记录，并同步更新首次触发的显式目标状态。"""
        if not delivered:
            return 0
        recorded = 0
        with self._session_factory() as session:
            for rec in delivered:
                item = rec.item
                try:
                    with session.begin_nested():
                        session.add(
                            ProfitAlertDelivery(
                                user_id=item.user_id,
                                lot_id=item.lot_id,
                                target_key=item.target_key,
                                target_signature=item.target_signature,
                                trigger_date=rec.trigger_date,
                                channel_type=rec.channel_type,
                                triggered_price=item.last_price,
                                target_price=item.target_price,
                                is_estimated=item.is_estimated,
                                sent_at=now,
                            )
                        )
                        session.flush()
                    recorded += 1
                except IntegrityError:
                    # 并发或重复评估已落库时幂等忽略。
                    continue

                if item.explicit_target_id is not None:
                    target_row = session.get(ProfitTarget, item.explicit_target_id)
                    if target_row is not None and target_row.status == TargetStatus.ACTIVE.value:
                        target_row.status = TargetStatus.TRIGGERED.value
                        target_row.triggered_at = now
            session.commit()
        return recorded

    def _record_buyback_deliveries(
        self,
        delivered: Sequence[_DeliveredBuybackRecord],
        now: datetime,
    ) -> int:
        """在短写事务中幂等持久化成功送达的买回价提醒记录。"""
        if not delivered:
            return 0
        recorded = 0
        with self._session_factory() as session:
            for rec in delivered:
                item = rec.item
                try:
                    with session.begin_nested():
                        session.add(
                            BuybackAlertDelivery(
                                user_id=item.user_id,
                                account_id=item.account_id,
                                security_id=item.security_id,
                                watchlist_item_id=item.watchlist_item_id,
                                target_signature=item.target_signature,
                                trigger_date=rec.trigger_date,
                                channel_type=rec.channel_type,
                                triggered_price=item.last_price,
                                target_price=item.target_price,
                                is_estimated=item.is_estimated,
                                sent_at=now,
                            )
                        )
                        session.flush()
                    recorded += 1
                except IntegrityError:
                    continue
            session.commit()
        return recorded

    async def evaluate_and_dispatch(
        self,
        symbols: Sequence[str],
        *,
        now: datetime | None = None,
    ) -> int:
        """针对指定变更标的执行一次完整的止盈与目标买回价评估、外部推送与去重落库，返回成功推送笔数。"""
        if not symbols:
            return 0
        current = now or self._now()
        triggerable: dict[str, QuoteSnapshot] = {}
        for raw_symbol in symbols:
            canonical = canonical_symbol(raw_symbol)
            snapshot = self._quote_hub.get_effective_quote(canonical, now=current)
            if self._is_triggerable_snapshot(canonical, snapshot, now=current):
                assert snapshot is not None
                triggerable[canonical] = snapshot
        if not triggerable:
            return 0

        trigger_date, jobs = await asyncio.to_thread(
            self._collect_dispatch_jobs, triggerable, current
        )
        buyback_date, buyback_jobs = await asyncio.to_thread(
            self._collect_buyback_dispatch_jobs, triggerable, current
        )
        if not jobs and not buyback_jobs:
            return 0

        delivered_records: list[_DeliveredRecord] = []
        for job in jobs:
            channel = self._channels.get(job.channel_type)
            if channel is None:
                continue
            batches = build_take_profit_markdown_batches(job.items, triggered_at=current)
            job_failed = False
            for batch_items, message in batches:
                ok = await self._send_with_retry(channel, job.target_url, message)
                if ok:
                    for item in batch_items:
                        delivered_records.append(
                            _DeliveredRecord(
                                item=item,
                                channel_type=job.channel_type,
                                trigger_date=trigger_date,
                            )
                        )
                else:
                    job_failed = True
                    break
            if job_failed and self._failure_cooldown_seconds > 0:
                self._failure_cooldown_until[(job.user_id, job.channel_type)] = current + timedelta(
                    seconds=self._failure_cooldown_seconds
                )

        if delivered_records:
            await asyncio.to_thread(self._record_deliveries, delivered_records, current)

        delivered_buyback_records: list[_DeliveredBuybackRecord] = []
        for buyback_job in buyback_jobs:
            if self._in_failure_cooldown(buyback_job.user_id, buyback_job.channel_type, current):
                continue
            channel = self._channels.get(buyback_job.channel_type)
            if channel is None:
                continue
            buyback_batches = build_buyback_markdown_batches(
                buyback_job.items, triggered_at=current
            )
            job_failed = False
            for batch_items, message in buyback_batches:
                ok = await self._send_with_retry(channel, buyback_job.target_url, message)
                if ok:
                    for item in batch_items:
                        delivered_buyback_records.append(
                            _DeliveredBuybackRecord(
                                item=item,
                                channel_type=buyback_job.channel_type,
                                trigger_date=buyback_date,
                            )
                        )
                else:
                    job_failed = True
                    break
            if job_failed and self._failure_cooldown_seconds > 0:
                self._failure_cooldown_until[
                    (buyback_job.user_id, buyback_job.channel_type)
                ] = current + timedelta(seconds=self._failure_cooldown_seconds)

        if delivered_buyback_records:
            await asyncio.to_thread(
                self._record_buyback_deliveries, delivered_buyback_records, current
            )
        return len(delivered_records) + len(delivered_buyback_records)
