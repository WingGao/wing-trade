"""长桥 OpenAPI 行情适配层：支持代码映射、基线补齐、增量推送合并与断线重连。"""

from __future__ import annotations

import asyncio
import importlib
import logging
import re
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.precision import ensure_decimal, round_price

from .calendar import AShareTradingCalendar, MarketSessionPhase, USTradingCalendar
from .provider import (
    CompositeQuoteProvider,
    ProviderHealth,
    QuoteProvider,
    QuoteSnapshot,
    canonical_symbol,
)

logger = logging.getLogger(__name__)

_ASHARE_CODE_RE = re.compile(r"^\d{6}$")
_US_CODE_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,11}$")
_LONGBRIDGE_SUPPORTED_MARKETS = frozenset({"SH", "SZ", "US"})
_DEFAULT_MAX_SUBSCRIPTIONS = 500
_DEFAULT_QUOTE_BATCH_SIZE = 500
_DEFAULT_RECONNECT_DELAYS: tuple[float, ...] = (5.0, 10.0, 20.0, 60.0)


def _now_utc() -> datetime:
    """返回当前 UTC 时间。"""
    return datetime.now(UTC)


def to_longbridge_symbol(canonical: str) -> str | None:
    """将项目规范代码（如 SH:600519、US:AAPL）转换为长桥代码，不支持市场返回 None。"""
    normalized = canonical_symbol(canonical)
    if ":" not in normalized:
        return None
    market, code = normalized.split(":", 1)
    if market in {"SH", "SZ"}:
        if not _ASHARE_CODE_RE.match(code):
            return None
        return f"{code}.{market}"
    if market == "US":
        if not _US_CODE_RE.match(code):
            return None
        return f"{code}.US"
    return None


def from_longbridge_symbol(lb_symbol: str) -> str | None:
    """将长桥代码（如 600519.SH、BRK.B.US）反向转换为项目规范代码。"""
    raw = str(lb_symbol or "").strip().upper()
    if "." not in raw:
        return None
    code, market = raw.rsplit(".", 1)
    if market not in _LONGBRIDGE_SUPPORTED_MARKETS or not code:
        return None
    if market in {"SH", "SZ"} and not _ASHARE_CODE_RE.match(code):
        return None
    if market == "US" and not _US_CODE_RE.match(code):
        return None
    return f"{market}:{code}"


def is_longbridge_supported_symbol(symbol: str) -> bool:
    """判断规范证券代码是否由长桥实时通道支持（SH/SZ/US）。"""
    return to_longbridge_symbol(symbol) is not None


def _to_optional_decimal(value: Any, *, field_name: str) -> Decimal | None:
    """将长桥 SDK 数值字段安全转为有限 Decimal，空值或非法值返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, (int, str)):
        text = str(value).strip()
        if not text:
            return None
        try:
            parsed = ensure_decimal(text, field=field_name)
            return parsed if parsed.is_finite() else None
        except (TypeError, ValueError):
            return None
    if isinstance(value, float):
        try:
            parsed = Decimal(str(value))
            return parsed if parsed.is_finite() else None
        except InvalidOperation:
            return None
    return None


def _normalize_timestamp(value: Any) -> datetime | None:
    """将长桥时间戳（datetime 或 Unix 秒）规整为 UTC datetime。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value <= 0:
            return None
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def map_trade_session(
    trade_session: Any,
    *,
    market: str,
    at: datetime,
    a_calendar: AShareTradingCalendar,
    us_calendar: USTradingCalendar,
) -> MarketSessionPhase:
    """将长桥 trade_session 枚举/文本映射为项目统一交易阶段。"""
    raw = ""
    if trade_session is not None:
        raw = str(getattr(trade_session, "name", trade_session)).strip().lower()
    if "pre" in raw:
        return "pre"
    if "post" in raw or "overnight" in raw:
        return "post"
    if "normal" in raw or "intraday" in raw or "regular" in raw:
        return "regular"
    if market == "US":
        return us_calendar.session_phase(at)
    return a_calendar.session_phase(at)


@dataclass(frozen=True, slots=True)
class LongbridgeCredentials:
    """长桥 API Key 静态凭据与可选网关参数。"""

    app_key: str
    app_secret: str
    access_token: str
    enable_overnight: bool = False
    http_url: str = ""
    quote_ws_url: str = ""


@dataclass(frozen=True, slots=True)
class LongbridgeBaseline:
    """单只证券在长桥推送通道中的基线参考价（用于为增量 PushQuote 补齐涨跌幅）。"""

    prev_close: Decimal | None = None
    regular_close: Decimal | None = None
    last_snapshot: QuoteSnapshot | None = None


def parse_security_quote(
    item: Any,
    *,
    fetched_at: datetime,
    a_calendar: AShareTradingCalendar,
    us_calendar: USTradingCalendar,
    provider: str = "longbridge",
) -> tuple[str, QuoteSnapshot, LongbridgeBaseline] | None:
    """将长桥 SecurityQuote 基线对象解析为统一快照与基线记录。"""
    canonical = from_longbridge_symbol(getattr(item, "symbol", ""))
    if canonical is None:
        return None
    market, _ = canonical.split(":", 1)

    regular_price = _to_optional_decimal(getattr(item, "last_done", None), field_name="last_done")
    prev_close = _to_optional_decimal(getattr(item, "prev_close", None), field_name="prev_close")
    if prev_close is not None and prev_close <= 0:
        prev_close = None
    regular_close = regular_price if regular_price is not None and regular_price > 0 else None
    source_time = _normalize_timestamp(getattr(item, "timestamp", None))

    if market == "US":
        current_phase = us_calendar.session_phase(fetched_at)
        pre_quote = getattr(item, "pre_market_quote", None)
        post_quote = getattr(item, "post_market_quote", None)
        overnight_quote = getattr(item, "overnight_quote", None)

        if current_phase == "pre" and pre_quote is not None:
            pre_price = _to_optional_decimal(
                getattr(pre_quote, "last_done", None), field_name="pre_last_done"
            )
            if pre_price is not None and pre_price > 0:
                pre_time = (
                    _normalize_timestamp(getattr(pre_quote, "timestamp", None)) or source_time
                )
                snapshot = QuoteSnapshot(
                    symbol=canonical,
                    last_price=pre_price,
                    prev_close=prev_close,
                    source_time=pre_time or fetched_at,
                    fetched_at=fetched_at,
                    provider=provider,
                    market_session="pre",
                )
                return (
                    canonical,
                    snapshot,
                    LongbridgeBaseline(
                        prev_close=prev_close,
                        regular_close=regular_close,
                        last_snapshot=snapshot,
                    ),
                )

        ext_quote = post_quote if current_phase == "post" else overnight_quote
        if current_phase in {"post", "closed"} and ext_quote is not None:
            ext_price = _to_optional_decimal(
                getattr(ext_quote, "last_done", None), field_name="ext_last_done"
            )
            if ext_price is not None and ext_price > 0:
                ext_time = (
                    _normalize_timestamp(getattr(ext_quote, "timestamp", None)) or source_time
                )
                ref_close = regular_close or prev_close
                change_pct = (
                    round_price((ext_price - ref_close) / ref_close * Decimal("100"))
                    if ref_close is not None and ref_close > 0
                    else None
                )
                snapshot = QuoteSnapshot(
                    symbol=canonical,
                    last_price=ext_price,
                    prev_close=ref_close,
                    change_pct=change_pct,
                    source_time=ext_time or fetched_at,
                    fetched_at=fetched_at,
                    provider=provider,
                    market_session="post",
                )
                return (
                    canonical,
                    snapshot,
                    LongbridgeBaseline(
                        prev_close=prev_close,
                        regular_close=regular_close,
                        last_snapshot=snapshot,
                    ),
                )

    if regular_close is None:
        return None

    session = (
        us_calendar.session_phase(source_time or fetched_at)
        if market == "US"
        else a_calendar.session_phase(source_time or fetched_at)
    )
    snapshot = QuoteSnapshot(
        symbol=canonical,
        last_price=regular_close,
        prev_close=prev_close,
        source_time=source_time,
        fetched_at=fetched_at,
        provider=provider,
        market_session=session,
    )
    return (
        canonical,
        snapshot,
        LongbridgeBaseline(
            prev_close=prev_close,
            regular_close=regular_close,
            last_snapshot=snapshot,
        ),
    )


def merge_push_quote(
    lb_symbol: str,
    event: Any,
    *,
    baseline: LongbridgeBaseline | None,
    fetched_at: datetime,
    a_calendar: AShareTradingCalendar,
    us_calendar: USTradingCalendar,
    provider: str = "longbridge",
) -> tuple[str, QuoteSnapshot, LongbridgeBaseline] | None:
    """将长桥增量 PushQuote 报文与内存基线合并为精确 Decimal 行情快照。"""
    canonical = from_longbridge_symbol(lb_symbol)
    if canonical is None:
        return None
    last_price = _to_optional_decimal(getattr(event, "last_done", None), field_name="last_done")
    if last_price is None or last_price <= 0:
        return None

    market, _ = canonical.split(":", 1)
    source_time = _normalize_timestamp(getattr(event, "timestamp", None))
    session = map_trade_session(
        getattr(event, "trade_session", None),
        market=market,
        at=source_time or fetched_at,
        a_calendar=a_calendar,
        us_calendar=us_calendar,
    )

    prev_close = baseline.prev_close if baseline is not None else None
    regular_close = baseline.regular_close if baseline is not None else None
    if session == "regular":
        regular_close = last_price

    if market == "US" and session == "post":
        ref_close = regular_close or prev_close
    else:
        ref_close = prev_close

    change_pct = (
        round_price((last_price - ref_close) / ref_close * Decimal("100"))
        if ref_close is not None and ref_close > 0
        else None
    )
    snapshot = QuoteSnapshot(
        symbol=canonical,
        last_price=last_price,
        prev_close=ref_close,
        change_pct=change_pct,
        source_time=source_time or fetched_at,
        fetched_at=fetched_at,
        provider=provider,
        market_session=session,
    )
    updated_baseline = LongbridgeBaseline(
        prev_close=prev_close,
        regular_close=regular_close,
        last_snapshot=snapshot,
    )
    return canonical, snapshot, updated_baseline


def _create_default_longbridge_context(credentials: LongbridgeCredentials) -> tuple[Any, Any]:
    """延迟导入 longbridge SDK 并通过 API Key 静态三元组创建 QuoteContext 与 SubType。"""
    openapi = importlib.import_module("longbridge.openapi")
    config_cls = getattr(openapi, "Config")
    quote_context_cls = getattr(openapi, "QuoteContext")
    sub_type = getattr(openapi, "SubType")

    kwargs: dict[str, Any] = {
        "app_key": credentials.app_key,
        "app_secret": credentials.app_secret,
        "access_token": credentials.access_token,
        "enable_overnight": credentials.enable_overnight,
        "enable_print_quote_packages": False,
    }
    if credentials.http_url:
        kwargs["http_url"] = credentials.http_url
    if credentials.quote_ws_url:
        kwargs["quote_ws_url"] = credentials.quote_ws_url
    config = config_cls.from_apikey(**kwargs)
    return quote_context_cls(config), sub_type


class LongbridgeQuoteProvider:
    """长桥同步批量行情源（实现 QuoteProvider 协议），供手动刷新与兜底查询复用。"""

    name = "longbridge"

    def __init__(
        self,
        credentials: LongbridgeCredentials,
        *,
        context_factory: Callable[[LongbridgeCredentials], tuple[Any, Any]] | None = None,
        now: Callable[[], datetime] | None = None,
        a_calendar: AShareTradingCalendar | None = None,
        us_calendar: USTradingCalendar | None = None,
        max_batch_size: int = _DEFAULT_QUOTE_BATCH_SIZE,
    ) -> None:
        """保存长桥凭据与可注入上下文工厂，构造阶段不建立网络连接。"""
        self._credentials = credentials
        self._context_factory = context_factory or _create_default_longbridge_context
        self._now = now or _now_utc
        self._a_calendar = a_calendar or AShareTradingCalendar()
        self._us_calendar = us_calendar or USTradingCalendar()
        self._max_batch_size = max(1, min(max_batch_size, _DEFAULT_QUOTE_BATCH_SIZE))
        self._lock = threading.Lock()
        self._ctx: Any | None = None

    def supports_symbol(self, symbol: str) -> bool:
        """判断证券是否属于长桥支持的沪深/美股市场。"""
        return is_longbridge_supported_symbol(symbol)

    def _get_context(self) -> Any:
        """按需延迟初始化并复用 QuoteContext。"""
        with self._lock:
            if self._ctx is None:
                self._ctx, _ = self._context_factory(self._credentials)
            return self._ctx

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        """按 500 只分批调用长桥 quote 接口并返回规范快照。"""
        lb_symbols: list[str] = []
        seen: set[str] = set()
        for symbol in symbols:
            lb = to_longbridge_symbol(symbol)
            if lb is None or lb in seen:
                continue
            seen.add(lb)
            lb_symbols.append(lb)
        if not lb_symbols:
            return {}

        ctx = self._get_context()
        result: dict[str, QuoteSnapshot] = {}
        for offset in range(0, len(lb_symbols), self._max_batch_size):
            batch = lb_symbols[offset : offset + self._max_batch_size]
            try:
                items = ctx.quote(batch)
            except Exception:
                # 建连/凭据异常时清空缓存上下文，便于下次重建。
                with self._lock:
                    self._ctx = None
                raise
            fetched_at = self._now()
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=UTC)
            for item in items:
                parsed = parse_security_quote(
                    item,
                    fetched_at=fetched_at,
                    a_calendar=self._a_calendar,
                    us_calendar=self._us_calendar,
                    provider=self.name,
                )
                if parsed is not None:
                    canonical, snapshot, _ = parsed
                    result[canonical] = snapshot
        return result

    def health(self) -> ProviderHealth:
        """报告长桥静态凭据已完成配置，不在健康检查时发起公网请求。"""
        configured = bool(
            self._credentials.app_key
            and self._credentials.app_secret
            and self._credentials.access_token
        )
        return ProviderHealth(
            provider=self.name,
            healthy=configured,
            message="长桥行情源已配置，连通性在建连/刷新时检查" if configured else "长桥凭据缺失",
        )


class PriorityFallbackQuoteProvider:
    """主备优先组合行情源：主源优先返回，未命中或主源失败的标的由备源补齐。"""

    def __init__(
        self,
        primary: QuoteProvider,
        fallback: QuoteProvider,
        *,
        name: str = "longbridge",
    ) -> None:
        """保存主行情源与备选兜底行情源。"""
        self.primary = primary
        self.fallback = fallback
        self.name = name

    def supports_symbol(self, symbol: str) -> bool:
        """主源或备源任一支持该证券即返回 True。"""
        return CompositeQuoteProvider._supports(
            self.primary, symbol
        ) or CompositeQuoteProvider._supports(self.fallback, symbol)

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        """优先用主源批量拉取支持的标的，再仅对缺失或不受支持的标的调用备源补齐。"""
        canonical_symbols = [canonical_symbol(symbol) for symbol in symbols]
        if not canonical_symbols:
            return {}
        result: dict[str, QuoteSnapshot] = {}
        primary_error: Exception | None = None
        primary_subset = [
            symbol
            for symbol in canonical_symbols
            if CompositeQuoteProvider._supports(self.primary, symbol)
        ]
        if primary_subset:
            try:
                result.update(self.primary.get_quotes(primary_subset))
            except Exception as exc:
                primary_error = exc
                logger.warning("主行情源 %s 拉取失败，将尝试回退备源: %s", self.primary.name, exc)

        missing = [
            symbol
            for symbol in canonical_symbols
            if symbol not in result and CompositeQuoteProvider._supports(self.fallback, symbol)
        ]
        if missing:
            try:
                result.update(self.fallback.get_quotes(missing))
            except Exception as exc:
                if not result:
                    raise (primary_error or exc)
        elif primary_error is not None and not result:
            raise primary_error
        return result

    def health(self) -> ProviderHealth:
        """汇总主备行情源状态，主源健康即报告健康。"""
        primary_health = self.primary.health()
        if primary_health.healthy:
            return primary_health
        return self.fallback.health()


class LongbridgeQuoteStream:
    """长桥 WebSocket 实时推流管理器：线程安全桥接 asyncio 事件循环并支持自动重连。"""

    def __init__(
        self,
        credentials: LongbridgeCredentials,
        *,
        context_factory: Callable[[LongbridgeCredentials], tuple[Any, Any]] | None = None,
        now: Callable[[], datetime] | None = None,
        a_calendar: AShareTradingCalendar | None = None,
        us_calendar: USTradingCalendar | None = None,
        max_subscriptions: int = _DEFAULT_MAX_SUBSCRIPTIONS,
        reconnect_delays: Sequence[float] = _DEFAULT_RECONNECT_DELAYS,
    ) -> None:
        """初始化长桥推流客户端与重连、订阅上限参数，构造时不建立连接。"""
        self._credentials = credentials
        self._context_factory = context_factory or _create_default_longbridge_context
        self._now = now or _now_utc
        self._a_calendar = a_calendar or AShareTradingCalendar()
        self._us_calendar = us_calendar or USTradingCalendar()
        self._max_subscriptions = max(1, min(max_subscriptions, _DEFAULT_MAX_SUBSCRIPTIONS))
        self._reconnect_delays = tuple(reconnect_delays) or _DEFAULT_RECONNECT_DELAYS

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._sync_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._on_snapshots: Callable[[dict[str, QuoteSnapshot]], None] | None = None
        self._on_status: Callable[[str, str], None] | None = None

        self._desired_lb_symbols: set[str] = set()
        self._active_lb_symbols: set[str] = set()
        self._baselines: dict[str, LongbridgeBaseline] = {}
        self._status: str = "idle"
        self._status_message: str = "未启动"

    @property
    def status(self) -> str:
        """返回当前推流连接状态（idle/connecting/live/reconnecting/error/stopped）。"""
        with self._lock:
            return self._status

    @property
    def subscribed_symbols(self) -> set[str]:
        """返回当前已成功订阅的规范证券代码集合。"""
        with self._lock:
            result: set[str] = set()
            for lb in self._active_lb_symbols:
                canonical = from_longbridge_symbol(lb)
                if canonical is not None:
                    result.add(canonical)
            return result

    def _set_status(self, state: str, message: str) -> None:
        """更新连接状态并线程安全通知主事件循环监听者。"""
        with self._lock:
            changed = state != self._status or message != self._status_message
            self._status = state
            self._status_message = message
            loop = self._loop
            callback = self._on_status
        if changed and loop is not None and callback is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(callback, state, message)
            except RuntimeError:
                pass

    def _emit_snapshots(self, snapshots: dict[str, QuoteSnapshot]) -> None:
        """将新收到的快照批量投递到主 asyncio 事件循环。"""
        if not snapshots:
            return
        with self._lock:
            loop = self._loop
            callback = self._on_snapshots
        if loop is not None and callback is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(callback, snapshots)
            except RuntimeError:
                pass

    def select_subscriptions(
        self,
        desired_symbols: Sequence[str] | set[str],
        *,
        priority_order: Sequence[str] | None = None,
    ) -> list[str]:
        """按优先级过滤并裁剪至长桥单连接订阅上限（默认 500 只），返回长桥代码列表。"""
        desired_set = {canonical_symbol(symbol) for symbol in desired_symbols}
        ordered_candidates: list[str] = []
        seen_canonical: set[str] = set()
        if priority_order is not None:
            for symbol in priority_order:
                canonical = canonical_symbol(symbol)
                if canonical in desired_set and canonical not in seen_canonical:
                    seen_canonical.add(canonical)
                    ordered_candidates.append(canonical)
        for canonical in sorted(desired_set):
            if canonical not in seen_canonical:
                seen_canonical.add(canonical)
                ordered_candidates.append(canonical)

        supported_lb: list[str] = []
        seen_lb: set[str] = set()
        for canonical in ordered_candidates:
            lb = to_longbridge_symbol(canonical)
            if lb is None or lb in seen_lb:
                continue
            seen_lb.add(lb)
            supported_lb.append(lb)

        if len(supported_lb) > self._max_subscriptions:
            logger.warning(
                "长桥订阅标的数 %d 超过单连接上限 %d，将按优先级保留前 %d 只，其余回退 HTTP 兜底",
                len(supported_lb),
                self._max_subscriptions,
                self._max_subscriptions,
            )
            return supported_lb[: self._max_subscriptions]
        return supported_lb

    def sync_subscriptions(
        self,
        desired_symbols: Sequence[str] | set[str],
        *,
        priority_order: Sequence[str] | None = None,
    ) -> set[str]:
        """更新期望订阅集合并唤醒工作线程执行差集订阅/退订。"""
        selected = set(self.select_subscriptions(desired_symbols, priority_order=priority_order))
        with self._lock:
            self._desired_lb_symbols = selected
        self._sync_event.set()
        return {
            canonical
            for lb in selected
            if (canonical := from_longbridge_symbol(lb)) is not None
        }

    def start(
        self,
        loop: asyncio.AbstractEventLoop,
        on_snapshots: Callable[[dict[str, QuoteSnapshot]], None],
        *,
        on_status: Callable[[str, str], None] | None = None,
    ) -> None:
        """启动后台推流工作线程，将推送回调安全桥接到目标事件循环。"""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._loop = loop
            self._on_snapshots = on_snapshots
            self._on_status = on_status
            self._stop_event.clear()
            self._sync_event.set()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="longbridge-quote-stream",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        """优雅停止后台推流线程并清理订阅状态。"""
        self._stop_event.set()
        self._sync_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        with self._lock:
            self._thread = None
            self._active_lb_symbols.clear()
        self._set_status("stopped", "已停止")

    def _handle_push_quote(self, lb_symbol: str, event: Any) -> None:
        """处理长桥 SDK 后台线程回调：与基线合并并投递到主事件循环。"""
        canonical = from_longbridge_symbol(lb_symbol)
        if canonical is None:
            return
        fetched_at = self._now()
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=UTC)
        with self._lock:
            baseline = self._baselines.get(canonical)
        merged = merge_push_quote(
            lb_symbol,
            event,
            baseline=baseline,
            fetched_at=fetched_at,
            a_calendar=self._a_calendar,
            us_calendar=self._us_calendar,
        )
        if merged is None:
            return
        resolved_symbol, snapshot, updated_baseline = merged
        with self._lock:
            self._baselines[resolved_symbol] = updated_baseline
        self._emit_snapshots({resolved_symbol: snapshot})

    def _apply_subscription_diff(self, ctx: Any, sub_type: Any) -> None:
        """在工作线程中执行新增标的基线拉取+订阅，以及移除标的退订。"""
        with self._lock:
            desired = set(self._desired_lb_symbols)
            active = set(self._active_lb_symbols)

        added = sorted(desired - active)
        removed = sorted(active - desired)

        if removed:
            ctx.unsubscribe(removed, [sub_type.Quote])
            with self._lock:
                self._active_lb_symbols.difference_update(removed)

        if added:
            baseline_snapshots: dict[str, QuoteSnapshot] = {}
            fetched_at = self._now()
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=UTC)
            for item in ctx.quote(added):
                parsed = parse_security_quote(
                    item,
                    fetched_at=fetched_at,
                    a_calendar=self._a_calendar,
                    us_calendar=self._us_calendar,
                )
                if parsed is not None:
                    canonical, snapshot, baseline = parsed
                    with self._lock:
                        self._baselines[canonical] = baseline
                    baseline_snapshots[canonical] = snapshot
            if baseline_snapshots:
                self._emit_snapshots(baseline_snapshots)

            ctx.subscribe(added, [sub_type.Quote], is_first_push=True)
            with self._lock:
                self._active_lb_symbols.update(added)

    def _run_loop(self) -> None:
        """后台工作线程主循环：负责长桥建连、差集订阅与指数退避重连。"""
        attempt = 0
        while not self._stop_event.is_set():
            ctx: Any | None = None
            try:
                self._set_status(
                    "connecting" if attempt == 0 else "reconnecting",
                    "正在连接长桥行情流",
                )
                ctx, sub_type = self._context_factory(self._credentials)
                ctx.set_on_quote(self._handle_push_quote)
                with self._lock:
                    self._active_lb_symbols.clear()
                self._apply_subscription_diff(ctx, sub_type)
                attempt = 0
                self._set_status("live", "长桥实时推流已连接")

                while not self._stop_event.is_set():
                    if self._sync_event.wait(timeout=1.0):
                        self._sync_event.clear()
                        if self._stop_event.is_set():
                            break
                        self._apply_subscription_diff(ctx, sub_type)
            except Exception as exc:
                if self._stop_event.is_set():
                    break
                delay = self._reconnect_delays[min(attempt, len(self._reconnect_delays) - 1)]
                attempt += 1
                logger.warning(
                    "长桥行情推流异常，将在 %.1f 秒后重试（第 %d 次）: %s",
                    delay,
                    attempt,
                    exc,
                )
                self._set_status("reconnecting", f"长桥行情流重连中（{exc}）")
                self._stop_event.wait(timeout=delay)
            finally:
                if ctx is not None:
                    close_fn = getattr(ctx, "close", None)
                    if callable(close_fn):
                        try:
                            close_fn()
                        except Exception:
                            pass
