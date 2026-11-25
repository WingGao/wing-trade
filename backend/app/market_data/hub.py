"""进程级内存行情中心（MarketDataHub）：管理内存报价、订阅池、基金估算联动、节流落盘与 SSE 广播。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.domain.markets import currency_for_market
from app.domain.time import format_beijing_iso
from app.models.ledger import Account, Lot, Security, WatchlistItem

from .longbridge_adapter import (
    LongbridgeCredentials,
    LongbridgeQuoteStream,
    is_longbridge_supported_symbol,
)
from .provider import QuoteProvider, QuoteSnapshot, canonical_symbol
from .service import QuoteService

logger = logging.getLogger("ashare_ledger.market_data.hub")


def _now_utc() -> datetime:
    """返回当前 UTC 时间。"""
    return datetime.now(UTC)


def _fmt_decimal(value: Decimal | None, digits: int = 6) -> str | None:
    """将 Decimal 格式化为固定小数位字符串，保持与 REST DTO 一致。"""
    if value is None:
        return None
    return format(value, f".{digits}f")


def _fmt_dt(value: datetime | None) -> str | None:
    """将 datetime 统一转换为带 +08:00 的北京时间 ISO-8601 字符串。"""
    return format_beijing_iso(value)


@dataclass(frozen=True, slots=True)
class SecurityMeta:
    """内存中缓存的轻量证券元数据，避免 SSE 序列化和基金估算时频繁查库。"""

    id: str
    market: str
    symbol: str
    canonical: str
    proxy_symbol: str | None = None
    proxy_name: str | None = None
    tracking_ratio: Decimal | None = None

    def as_security_like(self) -> Any:
        """构造满足 QuoteService 日历与估算接口的轻量只读对象。"""
        return SimpleNamespace(
            id=self.id,
            market=self.market,
            symbol=self.symbol,
            proxy_symbol=self.proxy_symbol,
            proxy_name=self.proxy_name,
            tracking_ratio=self.tracking_ratio,
        )


@dataclass(slots=True)
class StreamSubscriber:
    """单个活跃 SSE 客户端订阅会话。"""

    subscriber_id: str
    user_id: str
    account_id: str | None
    ephemeral_symbols: frozenset[str]
    allowed_symbols: set[str] = field(default_factory=set)
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=lambda: asyncio.Queue(maxsize=64))


class MarketDataHub:
    """进程级内存行情中心：承接长桥推流/轮询兜底、维护内存报价并向 SSE 客户端聚合推送。"""

    def __init__(
        self,
        *,
        quote_service: QuoteService,
        provider: QuoteProvider,
        session_factory: Callable[[], Session],
        settings: Settings | None = None,
        stream_factory: Callable[[LongbridgeCredentials], LongbridgeQuoteStream] | None = None,
        broadcast_coalesce_seconds: float = 0.3,
        flush_interval_seconds: float = 30.0,
        fallback_poll_interval_seconds: float = 10.0,
        fund_poll_interval_seconds: float = 600.0,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        """初始化内存行情中心状态与调度参数，构造时不建立任何网络连接。"""
        self.quote_service = quote_service
        self.provider = provider
        self._session_factory = session_factory
        self._settings = settings
        self._stream_factory = stream_factory
        self._broadcast_coalesce_seconds = max(0.0, broadcast_coalesce_seconds)
        self._flush_interval_seconds = max(1.0, flush_interval_seconds)
        self._fallback_poll_interval_seconds = max(2.0, fallback_poll_interval_seconds)
        self._fund_poll_interval_seconds = max(30.0, fund_poll_interval_seconds)
        self._now = now or _now_utc

        self._quotes: dict[str, QuoteSnapshot] = {}
        self._dirty_symbols: set[str] = set()
        self._security_meta: dict[str, SecurityMeta] = {}
        self._base_symbols: set[str] = set()
        self._priority_symbols: list[str] = []
        self._ephemeral_refs: dict[str, int] = {}
        self._fund_proxies: dict[str, list[SecurityMeta]] = {}
        self._subscribers: dict[str, StreamSubscriber] = {}
        self._quote_listeners: list[Callable[[list[str]], None]] = []

        self._pending_broadcast: dict[str, QuoteSnapshot] = {}
        self._broadcast_handle: asyncio.TimerHandle | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: list[asyncio.Task[Any]] = []
        self._stream: LongbridgeQuoteStream | None = None
        self._running = False
        self._provider_name = settings.quote_provider if settings is not None else provider.name
        self._upstream_state = "idle"
        self._upstream_message = "未启动"
        self._last_fund_poll_at: datetime | None = None

    @property
    def provider_name(self) -> str:
        """返回当前配置的行情提供方名称。"""
        return self._provider_name

    @property
    def upstream_state(self) -> str:
        """返回上游行情流当前运行状态。"""
        return self._upstream_state

    @property
    def is_running(self) -> bool:
        """返回行情中心是否已启动。"""
        return self._running

    def is_push_active(self) -> bool:
        """判断是否处于活跃内存推流状态（用于 REST auto 模式零阻塞直读）。"""
        return self._running and self._upstream_state == "live" and self._provider_name != "mock"

    def desired_symbols(self) -> set[str]:
        """返回当前需要关注的全部去重标的（持久范围 ∪ SSE 临时关注）。"""
        return set(self._base_symbols) | set(self._ephemeral_refs.keys())

    def _desired_priority_order(self) -> list[str]:
        """返回按持仓 > proxy > 自选 > 临时查看排列的订阅优先级列表。"""
        seen: set[str] = set()
        ordered: list[str] = []
        for symbol in self._priority_symbols:
            if symbol not in seen:
                seen.add(symbol)
                ordered.append(symbol)
        for symbol in sorted(self._ephemeral_refs):
            if symbol not in seen:
                seen.add(symbol)
                ordered.append(symbol)
        return ordered

    def warm_from_db(self, session: Session | None = None) -> None:
        """冷启动预热：从 quote_cache 加载已有快照并初始化订阅范围与基金代理映射。"""
        if session is not None:
            self._warm_with_session(session)
            return
        with self._session_factory() as managed:
            self._warm_with_session(managed)

    def _warm_with_session(self, session: Session) -> None:
        """在指定数据库会话中完成内存预热与范围扫描。"""
        self._reload_scope_with_session(session)
        now = self._now()
        securities = list(session.scalars(select(Security)).all())
        for security in securities:
            raw = self.quote_service._read_raw_cached(
                session, security, now=now, prefer_hub=False
            )
            if raw is not None:
                self._quotes[raw.symbol] = raw

    def refresh_scope_from_db(self, session: Session | None = None) -> None:
        """重新扫描数据库持仓/自选/基金代理配置，并同步上游订阅与在线客户端过滤集。"""
        try:
            if session is not None:
                self._reload_scope_with_session(session)
            else:
                with self._session_factory() as managed:
                    self._reload_scope_with_session(managed)
            self._sync_stream_subscriptions()
        except Exception as exc:
            logger.warning("刷新行情订阅范围失败: %s", exc)

    def _reload_scope_with_session(self, session: Session) -> None:
        """在给定会话内重建证券元数据、持久订阅池、基金代理映射与在线用户可见集合。"""
        securities = list(session.scalars(select(Security)).all())
        by_id: dict[str, SecurityMeta] = {}
        by_symbol: dict[str, SecurityMeta] = {}
        fund_proxies: dict[str, list[SecurityMeta]] = {}
        for sec in securities:
            canonical = QuoteService.security_symbol(sec)
            proxy_sym = canonical_symbol(sec.proxy_symbol) if sec.proxy_symbol else None
            meta = SecurityMeta(
                id=sec.id,
                market=sec.market.upper(),
                symbol=sec.symbol.upper(),
                canonical=canonical,
                proxy_symbol=proxy_sym,
                proxy_name=sec.proxy_name,
                tracking_ratio=sec.tracking_ratio,
            )
            by_id[sec.id] = meta
            by_symbol[canonical] = meta
            if meta.market == "OF" and meta.proxy_symbol:
                fund_proxies.setdefault(meta.proxy_symbol, []).append(meta)

        open_lot_sec_ids = set(
            session.scalars(select(Lot.security_id).where(Lot.status == "OPEN")).all()
        )
        watch_sec_ids = set(session.scalars(select(WatchlistItem.security_id)).all())

        holding_symbols = sorted(
            by_id[sec_id].canonical for sec_id in open_lot_sec_ids if sec_id in by_id
        )
        watch_symbols = sorted(
            by_id[sec_id].canonical
            for sec_id in watch_sec_ids
            if sec_id in by_id and sec_id not in open_lot_sec_ids
        )
        proxy_symbols: list[str] = sorted(
            {
                proxy_sym
                for sec_id in (open_lot_sec_ids | watch_sec_ids)
                if sec_id in by_id and (proxy_sym := by_id[sec_id].proxy_symbol) is not None
            }
        )

        self._security_meta = by_symbol
        self._fund_proxies = fund_proxies
        self._base_symbols = set(holding_symbols) | set(proxy_symbols) | set(watch_symbols)
        self._priority_symbols = [*holding_symbols, *proxy_symbols, *watch_symbols]

        if self._subscribers:
            for subscriber in list(self._subscribers.values()):
                subscriber.allowed_symbols = self._resolve_user_allowed_symbols(
                    session,
                    subscriber.user_id,
                    account_id=subscriber.account_id,
                    ephemeral_symbols=subscriber.ephemeral_symbols,
                )

    def _resolve_user_allowed_symbols(
        self,
        session: Session,
        user_id: str,
        *,
        account_id: str | None = None,
        ephemeral_symbols: Iterable[str] = (),
    ) -> set[str]:
        """计算某个用户在指定账户（或全部账户）下允许接收的规范行情代码集合。"""
        stmt = select(Account.id).where(Account.user_id == user_id)
        if account_id is not None:
            stmt = stmt.where(Account.id == account_id)
        account_ids = list(session.scalars(stmt).all())
        allowed: set[str] = {canonical_symbol(sym) for sym in ephemeral_symbols if sym.strip()}
        if account_ids:
            scoped = self.quote_service.symbols_for_scope(session, account_ids=account_ids)
            allowed.update(scoped.keys())
        return allowed

    def _sync_stream_subscriptions(self) -> None:
        """将当前最新订阅池同步到长桥推流客户端（若已启用）。"""
        if self._stream is not None:
            self._stream.sync_subscriptions(
                self.desired_symbols(),
                priority_order=self._desired_priority_order(),
            )

    async def start(self) -> None:
        """在 FastAPI lifespan 启动时预热内存并按配置驱动长桥推流或后台采集。"""
        if self._running:
            return
        self._loop = asyncio.get_running_loop()
        self._running = True
        try:
            self.warm_from_db()
        except Exception as exc:
            logger.warning("行情中心冷启动预热失败（将继续运行）: %s", exc)

        if self._provider_name == "mock":
            self._upstream_state = "mock"
            self._upstream_message = "模拟行情模式"
            return

        if self._provider_name == "longbridge" and self._settings is not None:
            credentials = LongbridgeCredentials(
                app_key=self._settings.longbridge_app_key,
                app_secret=self._settings.longbridge_app_secret,
                access_token=self._settings.longbridge_access_token,
                enable_overnight=self._settings.longbridge_enable_overnight,
                http_url=self._settings.longbridge_http_url,
                quote_ws_url=self._settings.longbridge_quote_ws_url,
            )
            self._stream = (
                self._stream_factory(credentials)
                if self._stream_factory is not None
                else LongbridgeQuoteStream(credentials)
            )
            self._sync_stream_subscriptions()
            self._stream.start(
                self._loop,
                on_snapshots=self._on_stream_snapshots,
                on_status=self._on_stream_status,
            )
            self._tasks.append(asyncio.create_task(self._write_behind_loop()))
            self._tasks.append(asyncio.create_task(self._fallback_poll_loop(longbridge_mode=True)))
            return

        # eastmoney 默认模式：后台按交易时段定时采集写入内存，同样为前端提供 SSE 实时推送
        self._upstream_state = "live"
        self._upstream_message = "定时采集推流运行中"
        self._tasks.append(asyncio.create_task(self._write_behind_loop()))
        self._tasks.append(asyncio.create_task(self._fallback_poll_loop(longbridge_mode=False)))

    async def stop(self) -> None:
        """在应用关闭时优雅停止推流线程、后台任务，并把脏报价刷入数据库。"""
        self._running = False
        if self._broadcast_handle is not None:
            self._broadcast_handle.cancel()
            self._broadcast_handle = None
        if self._stream is not None:
            self._stream.stop()
            self._stream = None
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()
        self.close_all_subscribers()
        try:
            self.flush_dirty_to_db()
        except Exception as exc:
            logger.warning("停机前行情落盘失败: %s", exc)
        self._upstream_state = "idle"
        self._upstream_message = "已停止"

    def _on_stream_snapshots(self, snapshots: dict[str, QuoteSnapshot]) -> None:
        """接收长桥推流投递到主事件循环的新报价批次。"""
        self.publish(snapshots, mark_dirty=True)

    def _on_stream_status(self, state: str, message: str) -> None:
        """接收上游推流状态变化并广播给全部在线 SSE 客户端。"""
        self._upstream_state = state
        self._upstream_message = message
        event = {
            "event": "status",
            "data": {
                "provider": self._provider_name,
                "streamState": state,
                "message": message,
            },
        }
        for subscriber in list(self._subscribers.values()):
            self._enqueue(subscriber, event)

    def _refresh_staleness(
        self, symbol: str, snapshot: QuoteSnapshot, *, now: datetime
    ) -> QuoteSnapshot:
        """按证券所属市场日历实时判定内存快照的新鲜度。"""
        if snapshot.is_stale:
            return snapshot
        meta = self._security_meta.get(symbol)
        if meta is not None:
            calendar = self.quote_service.calendar_for_security(meta.as_security_like())
        else:
            market = symbol.split(":", 1)[0] if ":" in symbol else "SH"
            if market == "US":
                calendar = self.quote_service.us_calendar
            elif market == "OF":
                calendar = self.quote_service.fund_calendar
            else:
                calendar = self.quote_service.calendar
        if calendar.is_stale(snapshot.fetched_at, now=now):
            return snapshot.stale()
        return snapshot

    def get_raw_quote(self, symbol: str, *, now: datetime | None = None) -> QuoteSnapshot | None:
        """从内存读取单只证券的最新原始报价并按当前时间刷新过期状态。"""
        canonical = canonical_symbol(symbol)
        snapshot = self._quotes.get(canonical)
        if snapshot is None:
            return None
        current = now or self._now()
        return self._refresh_staleness(canonical, snapshot, now=current)

    def get_effective_quote(
        self,
        symbol: str,
        *,
        now: datetime | None = None,
        security: Any | None = None,
    ) -> QuoteSnapshot | None:
        """读取单只证券的有效报价（已绑定 proxy 的场外基金自动在内存中派生估算净值）。"""
        canonical = canonical_symbol(symbol)
        current = now or self._now()
        raw = self.get_raw_quote(canonical, now=current)
        if raw is None:
            return None
        target_sec = security
        if target_sec is None:
            meta = self._security_meta.get(canonical)
            if meta is not None:
                target_sec = meta.as_security_like()
        if (
            target_sec is not None
            and str(getattr(target_sec, "market", "")).upper() == "OF"
            and getattr(target_sec, "proxy_symbol", None)
        ):
            proxy_raw = self.get_raw_quote(str(target_sec.proxy_symbol), now=current)
            return self.quote_service.resolve_effective_snapshot(target_sec, raw, proxy_raw)
        return raw

    def has_fresh_quotes(self, symbols: Sequence[str], *, now: datetime | None = None) -> bool:
        """判断指定标的集合在内存中是否均已存在未过期快照。"""
        if not symbols:
            return True
        current = now or self._now()
        for symbol in symbols:
            effective = self.get_effective_quote(symbol, now=current)
            if effective is None or effective.is_stale:
                return False
        return True

    def publish(
        self,
        snapshots: Mapping[str, QuoteSnapshot] | Iterable[QuoteSnapshot],
        *,
        mark_dirty: bool = True,
    ) -> list[QuoteSnapshot]:
        """发布新行情到内存中心，联动重算受影响的场外基金估算净值并调度节流广播。"""
        items = list(snapshots.values()) if isinstance(snapshots, Mapping) else list(snapshots)
        if not items:
            return []

        now = self._now()
        published: list[QuoteSnapshot] = []
        affected_proxies: set[str] = set()

        for snap in items:
            canonical = canonical_symbol(snap.symbol)
            if snap.last_price <= 0:
                continue
            if not snap.is_estimated:
                self._quotes[canonical] = snap
                if mark_dirty and not snap.is_stale:
                    self._dirty_symbols.add(canonical)
            effective = self.get_effective_quote(canonical, now=now) or snap
            self._pending_broadcast[canonical] = effective
            published.append(effective)
            if canonical in self._fund_proxies:
                affected_proxies.add(canonical)

        # 参考标的价格变动时，在内存中 O(k) 立即派生绑定场外基金的估算净值并随同广播
        for proxy_symbol in affected_proxies:
            proxy_snap = self.get_raw_quote(proxy_symbol, now=now)
            for fund_meta in self._fund_proxies.get(proxy_symbol, ()):
                fund_raw = self.get_raw_quote(fund_meta.canonical, now=now)
                if fund_raw is None:
                    continue
                derived = self.quote_service.resolve_effective_snapshot(
                    fund_meta.as_security_like(), fund_raw, proxy_snap
                )
                if derived is not None:
                    self._pending_broadcast[fund_meta.canonical] = derived
                    published.append(derived)

        self._schedule_broadcast()
        if published and self._quote_listeners:
            published_symbols = list({canonical_symbol(item.symbol) for item in published})
            for listener in list(self._quote_listeners):
                try:
                    listener(published_symbols)
                except Exception as exc:
                    logger.warning("行情发布监听器回调失败: %s", exc)
        return published

    def add_quote_listener(self, listener: Callable[[list[str]], None]) -> None:
        """注册行情发布监听器，供止盈推送引擎等下游模块非阻塞接收变更标的。"""
        if listener not in self._quote_listeners:
            self._quote_listeners.append(listener)

    def remove_quote_listener(self, listener: Callable[[list[str]], None]) -> None:
        """注销已注册的行情发布监听器。"""
        if listener in self._quote_listeners:
            self._quote_listeners.remove(listener)

    def _schedule_broadcast(self) -> None:
        """按 300ms 节流窗口合并广播；窗口为 0 或无活跃事件循环时立即派发。"""
        if not self._pending_broadcast:
            return
        if self._broadcast_coalesce_seconds <= 0:
            self._flush_broadcast()
            return
        try:
            loop = self._loop or asyncio.get_running_loop()
        except RuntimeError:
            self._flush_broadcast()
            return
        if self._broadcast_handle is None:
            self._broadcast_handle = loop.call_later(
                self._broadcast_coalesce_seconds,
                self._flush_broadcast,
            )

    def _flush_broadcast(self) -> None:
        """将缓冲中的变动快照按订阅者权限过滤并推入各自的 SSE 队列。"""
        self._broadcast_handle = None
        if not self._pending_broadcast:
            return
        batch = dict(self._pending_broadcast)
        self._pending_broadcast.clear()
        if not self._subscribers:
            return
        server_time = _fmt_dt(self._now())
        for subscriber in list(self._subscribers.values()):
            matched = [
                self.format_quote_dto(symbol, snap)
                for symbol, snap in batch.items()
                if symbol in subscriber.allowed_symbols
            ]
            if not matched:
                continue
            self._enqueue(
                subscriber,
                {
                    "event": "quote",
                    "data": {
                        "serverTime": server_time,
                        "quotes": matched,
                    },
                },
            )

    @staticmethod
    def _enqueue(subscriber: StreamSubscriber, event: dict[str, Any]) -> None:
        """向订阅者有界队列投递事件；队列满时丢弃最旧一帧避免内存膨胀。"""
        try:
            subscriber.queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                subscriber.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                subscriber.queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def format_quote_dto(self, symbol: str, snapshot: QuoteSnapshot) -> dict[str, Any]:
        """将内存 QuoteSnapshot 序列化为与 REST 完全一致的 camelCase 行情 DTO。"""
        canonical = canonical_symbol(symbol)
        meta = self._security_meta.get(canonical)
        market = (
            meta.market
            if meta is not None
            else (canonical.split(":", 1)[0] if ":" in canonical else None)
        )
        currency = currency_for_market(market) if market else None
        return {
            "symbol": canonical,
            "securityId": meta.id if meta is not None else None,
            "market": market,
            "currency": currency,
            "lastPrice": _fmt_decimal(snapshot.last_price, 6),
            "prevClose": _fmt_decimal(snapshot.prev_close, 6),
            "changePct": _fmt_decimal(snapshot.change_pct, 6),
            "sourceTime": _fmt_dt(snapshot.source_time),
            "fetchedAt": _fmt_dt(snapshot.fetched_at),
            "provider": snapshot.provider,
            "isStale": snapshot.is_stale,
            "marketSession": snapshot.market_session,
            "isEstimated": snapshot.is_estimated,
            "officialNav": _fmt_decimal(snapshot.official_nav, 6),
            "officialNavDate": snapshot.official_nav_date,
            "proxySymbol": snapshot.proxy_symbol,
            "proxyName": snapshot.proxy_name,
            "proxyChangePct": _fmt_decimal(snapshot.proxy_change_pct, 6),
            "trackingRatio": _fmt_decimal(snapshot.tracking_ratio, 6),
        }

    def subscribe_client(
        self,
        user_id: str,
        *,
        account_id: str | None = None,
        extra_symbols: Sequence[str] = (),
        session: Session | None = None,
    ) -> tuple[StreamSubscriber, dict[str, Any]]:
        """注册新的 SSE 订阅者，更新临时标的引用计数并生成首帧 snapshot 负载。"""
        ephemeral = frozenset(canonical_symbol(sym) for sym in extra_symbols if sym.strip())
        if session is not None:
            allowed = self._resolve_user_allowed_symbols(
                session, user_id, account_id=account_id, ephemeral_symbols=ephemeral
            )
        else:
            with self._session_factory() as managed:
                allowed = self._resolve_user_allowed_symbols(
                    managed, user_id, account_id=account_id, ephemeral_symbols=ephemeral
                )

        subscriber = StreamSubscriber(
            subscriber_id=str(uuid.uuid4()),
            user_id=user_id,
            account_id=account_id,
            ephemeral_symbols=ephemeral,
            allowed_symbols=allowed,
        )
        self._subscribers[subscriber.subscriber_id] = subscriber
        changed = False
        for sym in ephemeral:
            prev = self._ephemeral_refs.get(sym, 0)
            self._ephemeral_refs[sym] = prev + 1
            if prev == 0:
                changed = True
        if changed:
            self._sync_stream_subscriptions()

        now = self._now()
        initial_quotes: list[dict[str, Any]] = []
        for sym in sorted(allowed):
            effective = self.get_effective_quote(sym, now=now)
            if effective is not None:
                initial_quotes.append(self.format_quote_dto(sym, effective))

        snapshot_payload = {
            "serverTime": _fmt_dt(now),
            "provider": self._provider_name,
            "streamState": self._upstream_state,
            "quotes": initial_quotes,
        }
        return subscriber, snapshot_payload

    def close_all_subscribers(self) -> None:
        """向全部在线 SSE 订阅者发送停机关闭信号，确保服务优雅退出不挂起长连接。"""
        for subscriber in list(self._subscribers.values()):
            self._enqueue(subscriber, {"event": "__close__", "data": {}})

    def unsubscribe_client(self, subscriber_id: str) -> None:
        """注销 SSE 订阅者并回收临时标的引用计数，必要时同步退订上游。"""
        subscriber = self._subscribers.pop(subscriber_id, None)
        if subscriber is None:
            return
        changed = False
        for sym in subscriber.ephemeral_symbols:
            remaining = self._ephemeral_refs.get(sym, 0) - 1
            if remaining <= 0:
                if sym in self._ephemeral_refs:
                    del self._ephemeral_refs[sym]
                    changed = True
            else:
                self._ephemeral_refs[sym] = remaining
        if changed:
            self._sync_stream_subscriptions()

    def flush_dirty_to_db(self, session: Session | None = None) -> int:
        """将内存中有更新的原始报价批量写回 quote_cache（Write-behind，不触发止盈提醒）。"""
        if not self._dirty_symbols:
            return 0
        symbols_to_flush = list(self._dirty_symbols)
        snapshots = [
            self._quotes[sym]
            for sym in symbols_to_flush
            if sym in self._quotes and not self._quotes[sym].is_stale
        ]
        self._dirty_symbols.clear()
        if not snapshots:
            return 0

        if session is not None:
            self.quote_service.upsert(session, snapshots, evaluate_targets=False)
            session.commit()
            return len(snapshots)
        with self._session_factory() as managed:
            self.quote_service.upsert(managed, snapshots, evaluate_targets=False)
            managed.commit()
        return len(snapshots)

    async def _write_behind_loop(self) -> None:
        """后台定时将脏行情写回数据库，避免高频推流造成 SQLite 写锁争用。"""
        while self._running:
            await asyncio.sleep(self._flush_interval_seconds)
            if not self._running:
                break
            try:
                await asyncio.to_thread(self.flush_dirty_to_db)
            except Exception as exc:
                logger.warning("后台行情节流落盘失败: %s", exc)

    def _select_poll_targets(self, *, longbridge_mode: bool, now: datetime) -> list[str]:
        """根据当前运行模式筛选需要通过 HTTP 兜底轮询的标的列表。"""
        desired = sorted(self.desired_symbols())
        if not desired:
            return []
        fund_due = (
            self._last_fund_poll_at is None
            or (now - self._last_fund_poll_at).total_seconds() >= self._fund_poll_interval_seconds
        )
        targets: list[str] = []
        a_open = self.quote_service.calendar.is_session_open(now)
        us_open = self.quote_service.us_calendar.is_session_open(now)
        stream_active = self._stream is not None and self._stream.status == "live"
        stream_subscribed = self._stream.subscribed_symbols if self._stream is not None else set()

        for sym in desired:
            market = sym.split(":", 1)[0] if ":" in sym else ""
            if market == "OF":
                if fund_due or sym not in self._quotes:
                    targets.append(sym)
                continue
            if (
                longbridge_mode
                and stream_active
                and is_longbridge_supported_symbol(sym)
                and sym in stream_subscribed
            ):
                # 已由长桥推流覆盖的标的无需 HTTP 轮询
                continue
            # 交易时段内实时轮询；休市时仅在内存尚无快照时拉取一次基线
            if market == "US":
                if us_open or sym not in self._quotes:
                    targets.append(sym)
            else:
                if a_open or sym not in self._quotes:
                    targets.append(sym)

        if any(sym.startswith("OF:") for sym in targets):
            self._last_fund_poll_at = now
        return targets

    async def _fallback_poll_loop(self, *, longbridge_mode: bool) -> None:
        """后台兜底采集协程：长桥模式下负责 OF/BJ/断流降级，eastmoney 模式下驱动实时内存更新。"""
        while self._running:
            try:
                now = self._now()
                targets = self._select_poll_targets(longbridge_mode=longbridge_mode, now=now)
                if targets:
                    fetched = await asyncio.to_thread(self.provider.get_quotes, targets)
                    if fetched:
                        self.publish(fetched, mark_dirty=True)
            except Exception as exc:
                logger.warning("后台兜底行情采集失败: %s", exc)
            await asyncio.sleep(self._fallback_poll_interval_seconds)
