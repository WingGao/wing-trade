from __future__ import annotations

import asyncio
import json
import re
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timezone
from decimal import Decimal
from typing import Any, Protocol, cast
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.domain.precision import ensure_decimal, round_price

from .calendar import AShareTradingCalendar, MarketSessionPhase, USTradingCalendar


def _now() -> datetime:
    """返回带 UTC 时区的当前时间。"""
    return datetime.now(timezone.utc)


def canonical_symbol(value: str) -> str:
    """Normalize provider symbols to the public ``MARKET:CODE`` form."""
    value = value.strip().upper()
    if ":" in value:
        market, symbol = value.split(":", 1)
        return f"{market}:{symbol}"
    return value


@dataclass(frozen=True, slots=True)
class QuoteSnapshot:
    """统一行情快照，支持场外基金盘中估算派生元数据。"""

    symbol: str
    last_price: Decimal
    prev_close: Decimal | None = None
    change_pct: Decimal | None = None
    source_time: datetime | None = None
    fetched_at: datetime = field(default_factory=_now)
    provider: str = "unknown"
    is_stale: bool = False
    market_session: MarketSessionPhase | None = None
    is_estimated: bool = False
    official_nav: Decimal | None = None
    official_nav_date: str | None = None
    proxy_symbol: str | None = None
    proxy_name: str | None = None
    proxy_change_pct: Decimal | None = None
    tracking_ratio: Decimal | None = None

    def __post_init__(self) -> None:
        """规范证券代码并校验、量化报价与估算数值。"""
        object.__setattr__(self, "symbol", canonical_symbol(self.symbol))
        last_price = round_price(ensure_decimal(self.last_price, field="last_price"))
        if last_price <= 0:
            raise ValueError("last_price must be positive")
        object.__setattr__(self, "last_price", last_price)
        if self.prev_close is not None:
            prev_close = round_price(ensure_decimal(self.prev_close, field="prev_close"))
            if prev_close <= 0:
                raise ValueError("prev_close must be positive")
            object.__setattr__(self, "prev_close", prev_close)
        if self.change_pct is None and self.prev_close not in (None, Decimal("0")):
            change = (self.last_price - self.prev_close) / self.prev_close * Decimal("100")
            object.__setattr__(self, "change_pct", round_price(change))
        elif self.change_pct is not None:
            object.__setattr__(self, "change_pct", round_price(ensure_decimal(self.change_pct, field="change_pct")))
        if self.official_nav is not None:
            official_nav = round_price(ensure_decimal(self.official_nav, field="official_nav"))
            if official_nav <= 0:
                raise ValueError("official_nav must be positive")
            object.__setattr__(self, "official_nav", official_nav)
        if self.proxy_symbol is not None:
            object.__setattr__(self, "proxy_symbol", canonical_symbol(self.proxy_symbol))
        if self.proxy_change_pct is not None:
            object.__setattr__(
                self,
                "proxy_change_pct",
                round_price(ensure_decimal(self.proxy_change_pct, field="proxy_change_pct")),
            )
        if self.tracking_ratio is not None:
            object.__setattr__(
                self,
                "tracking_ratio",
                round_price(ensure_decimal(self.tracking_ratio, field="tracking_ratio")),
            )

    def stale(self, *, at: datetime | None = None) -> QuoteSnapshot:
        """返回保留原价格、时间戳与估算元数据的过期快照。"""
        return QuoteSnapshot(
            symbol=self.symbol,
            last_price=self.last_price,
            prev_close=self.prev_close,
            change_pct=self.change_pct,
            source_time=self.source_time,
            fetched_at=self.fetched_at,
            provider=self.provider,
            is_stale=True,
            market_session=self.market_session,
            is_estimated=self.is_estimated,
            official_nav=self.official_nav,
            official_nav_date=self.official_nav_date,
            proxy_symbol=self.proxy_symbol,
            proxy_name=self.proxy_name,
            proxy_change_pct=self.proxy_change_pct,
            tracking_ratio=self.tracking_ratio,
        )


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    provider: str
    healthy: bool
    message: str | None = None
    checked_at: datetime = field(default_factory=_now)


class QuoteProvider(Protocol):
    name: str

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        ...

    def health(self) -> ProviderHealth:
        ...


class MockQuoteProvider:
    """Deterministic provider for tests and offline development."""

    name = "mock"

    def __init__(self, quotes: Mapping[str, QuoteSnapshot | Mapping[str, object]] | None = None) -> None:
        self.quotes: dict[str, QuoteSnapshot] = {}
        self.set_quotes(quotes or {})

    def set_quotes(self, quotes: Mapping[str, QuoteSnapshot | Mapping[str, object]]) -> None:
        """覆盖或补充可确定复现的模拟行情。"""
        for symbol, value in quotes.items():
            if isinstance(value, QuoteSnapshot):
                snapshot = value
            else:
                snapshot = QuoteSnapshot(
                    symbol=symbol,
                    provider=self.name,
                    **cast(dict[str, Any], value),
                )
            self.quotes[canonical_symbol(symbol)] = snapshot

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        """按请求顺序返回已配置的模拟行情。"""
        return {
            canonical_symbol(symbol): self.quotes[canonical_symbol(symbol)]
            for symbol in symbols
            if canonical_symbol(symbol) in self.quotes
        }

    def health(self) -> ProviderHealth:
        """报告模拟行情源可用。"""
        return ProviderHealth(provider=self.name, healthy=True)

    async def get_quotes_async(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        """在线程中执行同步模拟行情读取。"""
        return await asyncio.to_thread(self.get_quotes, symbols)


class FallbackQuoteProvider:
    """Return a fallback provider's data when the primary source fails."""

    name = "fallback"

    def __init__(self, primary: QuoteProvider, fallback: QuoteProvider) -> None:
        self.primary = primary
        self.fallback = fallback

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        """优先返回主行情源，失败时返回过期回退快照。"""
        try:
            return self.primary.get_quotes(symbols)
        except Exception:
            return {symbol: snapshot.stale() for symbol, snapshot in self.fallback.get_quotes(symbols).items()}

    def health(self) -> ProviderHealth:
        """返回首个健康行情源的健康状态。"""
        try:
            primary = self.primary.health()
            if primary.healthy:
                return primary
        except Exception:
            pass
        return self.fallback.health()


class AKShareQuoteProvider:
    """Optional AKShare adapter; the dependency is imported only on refresh."""

    name = "akshare"

    def __init__(self, *, akshare_module: object | None = None) -> None:
        self._module = akshare_module

    def _load(self) -> object:
        """延迟加载可选 AKShare 依赖。"""
        if self._module is None:
            import akshare  # type: ignore[import-not-found]

            self._module = akshare
        return self._module

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        """从 AKShare 全市场快照中提取请求证券。"""
        module = self._load()
        result: dict[str, QuoteSnapshot] = {}
        fetch = getattr(module, "stock_zh_a_spot_em")
        frame = fetch()
        records = list(getattr(frame, "to_dict", lambda **_: [])(orient="records"))
        for canonical in (canonical_symbol(value) for value in symbols):
            _, code = canonical.split(":", 1) if ":" in canonical else ("", canonical)
            # AKShare's spot endpoint is a synchronous SDK call.  The collector
            # invokes this method through asyncio.to_thread so it never blocks
            # the FastAPI event loop.
            row = None
            for candidate in records:
                candidate_code = str(candidate.get("代码", candidate.get("symbol", "")))
                if candidate_code == code:
                    row = candidate
                    break
            if row is None:
                continue
            last = row.get("最新价", row.get("last_price"))
            prev = row.get("昨收", row.get("prev_close"))
            if last is None:
                continue
            result[canonical] = QuoteSnapshot(
                symbol=canonical,
                last_price=last,
                prev_close=prev,
                change_pct=row.get("涨跌幅", row.get("change_pct")),
                source_time=_now(),
                provider=self.name,
            )
        return result

    def health(self) -> ProviderHealth:
        """只检查 AKShare 是否可导入，不发起网络请求。"""
        try:
            self._load()
        except Exception as exc:
            return ProviderHealth(provider=self.name, healthy=False, message=str(exc))
        return ProviderHealth(provider=self.name, healthy=True)


class EastmoneyQuoteProvider:
    """东方财富 A 股批量行情源，使用标准库 HTTP 和精确 Decimal 解析。"""

    name = "eastmoney"
    supported_markets = frozenset({"SH", "SZ", "BJ"})
    _endpoint = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    _fields = "f12,f13,f14,f2,f3,f18,f124"
    _max_response_bytes = 2 * 1024 * 1024

    def __init__(
        self,
        *,
        timeout_seconds: Decimal | str | int | float = Decimal("5"),
        transport: Callable[[Request, float], bytes] | None = None,
        now: Callable[[], datetime] | None = None,
        max_batch_size: int = 100,
        max_symbols: int = 1000,
    ) -> None:
        """校验超时和工作量限制并保存可注入传输。"""
        timeout = (
            Decimal(str(timeout_seconds))
            if isinstance(timeout_seconds, float)
            else ensure_decimal(timeout_seconds, field="timeout_seconds")
        )
        if not timeout.is_finite() or timeout <= 0:
            raise ValueError("timeout_seconds must be a finite positive number")
        if max_batch_size <= 0 or max_batch_size > 100:
            raise ValueError("max_batch_size must be between 1 and 100")
        if max_symbols <= 0:
            raise ValueError("max_symbols must be positive")
        self.timeout_seconds = timeout
        self._transport = transport or self._default_transport
        self._now = now or _now
        self.max_batch_size = max_batch_size
        self.max_symbols = max_symbols

    def supports_symbol(self, symbol: str) -> bool:
        """判断证券是否属于当前 A 股行情源支持的市场。"""
        canonical = canonical_symbol(symbol)
        if ":" not in canonical:
            return False
        market, code = canonical.split(":", 1)
        return market in self.supported_markets and len(code) == 6 and code.isdigit()

    def _default_transport(self, request: Request, timeout_seconds: float) -> bytes:
        """执行有响应大小上限的同步 HTTPS 请求。"""
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            content_length = response.headers.get("Content-Length")
            if content_length is not None and int(content_length) > self._max_response_bytes:
                raise ValueError("eastmoney response exceeds size limit")
            body = response.read(self._max_response_bytes + 1)
        if len(body) > self._max_response_bytes:
            raise ValueError("eastmoney response exceeds size limit")
        return body

    @staticmethod
    def _reject_json_constant(value: str) -> object:
        """拒绝 JSON 中的 NaN 和 Infinity 非有限常量。"""
        raise ValueError(f"invalid JSON numeric constant: {value}")

    @staticmethod
    def _optional_decimal(value: object, *, field_name: str) -> Decimal | None:
        """解析可缺失的有限十进制字段。"""
        if value in (None, "", "-"):
            return None
        return ensure_decimal(value, field=field_name)

    @staticmethod
    def _upstream_market(market: str) -> int:
        """把公开市场标识映射为东方财富市场编号。"""
        return 1 if market == "SH" else 0

    @staticmethod
    def _normalize_code(value: object) -> str:
        """把上游证券代码规整为六位数字字符串。"""
        raw = str(value).strip()
        if raw.endswith(".0"):
            raw = raw[:-2]
        return raw.zfill(6)

    @staticmethod
    def _source_time(value: object) -> datetime | None:
        """把上游 Unix 秒时间戳转换为 UTC 时间。"""
        if value in (None, "", "-"):
            return None
        try:
            seconds = int(ensure_decimal(value, field="source_time"))
            if seconds <= 0:
                return None
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, TypeError, ValueError):
            return None

    def _request_batch(self, symbols: Sequence[str]) -> list[dict[str, object]]:
        """请求一个不超过一百只证券的行情批次。"""
        secids: list[str] = []
        for symbol in symbols:
            market, code = canonical_symbol(symbol).split(":", 1)
            secids.append(f"{self._upstream_market(market)}.{code}")
        query = urlencode({"fltt": "2", "secids": ",".join(secids), "fields": self._fields})
        request = Request(
            f"{self._endpoint}?{query}",
            headers={
                "Accept": "application/json",
                "User-Agent": "wing-trade/0.1 (+backend market data)",
            },
        )
        body = self._transport(request, float(self.timeout_seconds))
        if len(body) > self._max_response_bytes:
            raise ValueError("eastmoney response exceeds size limit")
        text = body.decode("utf-8") if isinstance(body, bytes) else str(body)
        payload = json.loads(
            text,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=self._reject_json_constant,
        )
        if not isinstance(payload, Mapping):
            raise ValueError("eastmoney response must be an object")
        data = payload.get("data")
        if not isinstance(data, Mapping):
            return []
        rows = data.get("diff", [])
        if not isinstance(rows, list):
            raise ValueError("eastmoney data.diff must be a list")
        return [row for row in rows if isinstance(row, dict)]

    def _snapshot_from_row(
        self,
        row: Mapping[str, object],
        *,
        symbol: str,
        fetched_at: datetime,
    ) -> QuoteSnapshot | None:
        """把单条上游记录转换为有效报价，无效记录返回空。"""
        try:
            last_price = self._optional_decimal(row.get("f2"), field_name="last_price")
            prev_close = self._optional_decimal(row.get("f18"), field_name="prev_close")
            if last_price is None or last_price <= 0:
                return None
            if prev_close is not None and prev_close <= 0:
                return None
        except (TypeError, ValueError):
            return None
        try:
            change_pct = self._optional_decimal(row.get("f3"), field_name="change_pct")
        except (TypeError, ValueError):
            change_pct = None
        return QuoteSnapshot(
            symbol=symbol,
            last_price=last_price,
            prev_close=prev_close,
            change_pct=change_pct,
            source_time=self._source_time(row.get("f124")),
            fetched_at=fetched_at,
            provider=self.name,
        )

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        """批量读取支持的 A 股行情，并按规范证券代码返回精确快照。"""
        requested: list[str] = []
        seen: set[str] = set()
        for value in symbols:
            canonical = canonical_symbol(value)
            if canonical in seen or not self.supports_symbol(canonical):
                continue
            seen.add(canonical)
            requested.append(canonical)
        if not requested:
            return {}
        if len(requested) > self.max_symbols:
            raise ValueError("eastmoney quote request exceeds cumulative symbol limit")

        result: dict[str, QuoteSnapshot] = {}
        batch_error: Exception | None = None
        for offset in range(0, len(requested), self.max_batch_size):
            batch = requested[offset : offset + self.max_batch_size]
            try:
                rows = self._request_batch(batch)
            except Exception as exc:
                batch_error = exc
                continue
            fetched_at = self._now()
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=UTC)
            canonical_by_identity = {
                (self._upstream_market(canonical.split(":", 1)[0]), canonical.split(":", 1)[1]): canonical
                for canonical in batch
            }
            for row in rows:
                try:
                    identity = (int(str(row.get("f13"))), self._normalize_code(row.get("f12")))
                except (TypeError, ValueError):
                    continue
                resolved_symbol = canonical_by_identity.get(identity)
                if resolved_symbol is None:
                    continue
                snapshot = self._snapshot_from_row(
                    row, symbol=resolved_symbol, fetched_at=fetched_at
                )
                if snapshot is not None:
                    result[resolved_symbol] = snapshot
        if batch_error is not None and not result:
            raise batch_error
        return result

    def health(self) -> ProviderHealth:
        """报告行情源已完成配置，不在健康检查时访问公网。"""
        return ProviderHealth(
            provider=self.name,
            healthy=True,
            message="行情源已配置，连通性在刷新时检查",
        )


_US_SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,11}$")
_US_UPSTREAM_MARKETS = ("105", "106", "107")


class USMarketResolver:
    """线程安全的美股东方财富交易所编号（105/106/107）解析与 LRU 缓存器。"""

    _search_url = "https://searchapi.eastmoney.com/api/suggest/get"
    _max_response_bytes = 2 * 1024 * 1024

    def __init__(
        self,
        *,
        transport: Callable[[Request, float], bytes] | None = None,
        timeout_seconds: Decimal | str | int | float = Decimal("5"),
        max_cached: int = 512,
    ) -> None:
        """初始化美股市场码缓存与可注入 HTTP 传输。"""
        if max_cached <= 0:
            raise ValueError("max_cached must be positive")
        self._transport = transport or EastmoneyQuoteProvider()._default_transport
        self._timeout_seconds = float(
            Decimal(str(timeout_seconds))
            if isinstance(timeout_seconds, float)
            else ensure_decimal(timeout_seconds, field="timeout_seconds")
        )
        self._max_cached = max_cached
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._lock = threading.Lock()

    def get_cached(self, code: str) -> str | None:
        """读取已缓存的美股交易所编号，不发起网络请求。"""
        normalized = code.strip().upper()
        with self._lock:
            market = self._cache.get(normalized)
            if market is not None:
                self._cache.move_to_end(normalized)
            return market

    def remember(self, code: str, upstream_market: str) -> None:
        """将已确认的美股交易所编号写入有界 LRU 缓存。"""
        normalized = code.strip().upper()
        market = str(upstream_market).strip()
        if not normalized or market not in _US_UPSTREAM_MARKETS:
            return
        with self._lock:
            self._cache[normalized] = market
            self._cache.move_to_end(normalized)
            while len(self._cache) > self._max_cached:
                self._cache.popitem(last=False)

    def resolve(
        self,
        code: str,
        *,
        transport: Callable[[Request, float], bytes] | None = None,
        timeout_seconds: float | None = None,
    ) -> str | None:
        """优先读取缓存，未命中时调用东方财富查名接口解析美股交易所编号。"""
        normalized = code.strip().upper()
        if not normalized:
            return None
        cached = self.get_cached(normalized)
        if cached is not None:
            return cached
        query = urlencode(
            {
                "input": normalized,
                "type": "14",
                "token": "D43BF722C8E33BDC906FB84D85E326E8",
                "count": "20",
            }
        )
        request = Request(
            f"{self._search_url}?{query}",
            headers={
                "Accept": "application/json",
                "User-Agent": "wing-trade/0.1 (+backend market data)",
                "Referer": "https://quote.eastmoney.com/",
            },
        )
        active_transport = transport or self._transport
        active_timeout = timeout_seconds if timeout_seconds is not None else self._timeout_seconds
        try:
            body = active_transport(request, active_timeout)
            if len(body) > self._max_response_bytes:
                return None
            payload = json.loads(
                body.decode("utf-8-sig") if isinstance(body, bytes) else str(body),
                parse_float=Decimal,
                parse_constant=EastmoneyQuoteProvider._reject_json_constant,
            )
        except Exception:
            return None
        if not isinstance(payload, Mapping):
            return None
        quotation = payload.get("QuotationCodeTable")
        if not isinstance(quotation, Mapping):
            return None
        rows = quotation.get("Data")
        if not isinstance(rows, list):
            return None
        matched: str | None = None
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            row_code = str(row.get("Code") or "").strip().upper()
            upstream_market = str(row.get("MktNum", row.get("MarketType", ""))).strip()
            if upstream_market in _US_UPSTREAM_MARKETS and row_code:
                self.remember(row_code, upstream_market)
                if row_code == normalized and matched is None:
                    matched = upstream_market
        return matched


_SHARED_US_MARKET_RESOLVER = USMarketResolver()


def shared_us_market_resolver() -> USMarketResolver:
    """返回进程级共享的美股交易所编号解析器，供批量行情与分时共用。"""
    return _SHARED_US_MARKET_RESOLVER


class EastmoneyUSQuoteProvider:
    """东方财富美股批量行情源，支持常规交易时段与盘前/盘后扩展时段报价。"""

    name = "eastmoney-us"
    supported_markets = frozenset({"US"})
    _endpoint = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    _fields = "f12,f13,f14,f2,f3,f18,f124,f251,f253,f292"
    _max_response_bytes = 2 * 1024 * 1024

    def __init__(
        self,
        *,
        timeout_seconds: Decimal | str | int | float = Decimal("5"),
        transport: Callable[[Request, float], bytes] | None = None,
        now: Callable[[], datetime] | None = None,
        resolver: USMarketResolver | None = None,
        calendar: USTradingCalendar | None = None,
        max_batch_size: int = 100,
        max_symbols: int = 500,
    ) -> None:
        """校验超时和批量限制，并保存可注入传输、时钟与美股市场码解析器。"""
        timeout = (
            Decimal(str(timeout_seconds))
            if isinstance(timeout_seconds, float)
            else ensure_decimal(timeout_seconds, field="timeout_seconds")
        )
        if not timeout.is_finite() or timeout <= 0:
            raise ValueError("timeout_seconds must be a finite positive number")
        if max_batch_size <= 0 or max_batch_size > 100:
            raise ValueError("max_batch_size must be between 1 and 100")
        if max_symbols <= 0:
            raise ValueError("max_symbols must be positive")
        self.timeout_seconds = timeout
        self._transport = transport or EastmoneyQuoteProvider()._default_transport
        self._now = now or _now
        self._resolver = (
            resolver
            if resolver is not None
            else (
                USMarketResolver(transport=transport, timeout_seconds=timeout)
                if transport is not None
                else shared_us_market_resolver()
            )
        )
        self._calendar = calendar or USTradingCalendar()
        self.max_batch_size = max_batch_size
        self.max_symbols = max_symbols

    def supports_symbol(self, symbol: str) -> bool:
        """判断证券是否属于美股市场且代码格式合法。"""
        canonical = canonical_symbol(symbol)
        if ":" not in canonical:
            return False
        market, code = canonical.split(":", 1)
        return market in self.supported_markets and bool(_US_SYMBOL_PATTERN.match(code))

    def _request_secids(self, secids: Sequence[str]) -> list[dict[str, object]]:
        """按东方财富 secid 列表批量请求美股行情快照。"""
        query = urlencode({"fltt": "2", "invt": "2", "secids": ",".join(secids), "fields": self._fields})
        request = Request(
            f"{self._endpoint}?{query}",
            headers={
                "Accept": "application/json",
                "User-Agent": "wing-trade/0.1 (+backend market data)",
                "Referer": "https://quote.eastmoney.com/",
            },
        )
        body = self._transport(request, float(self.timeout_seconds))
        if len(body) > self._max_response_bytes:
            raise ValueError("eastmoney us response exceeds size limit")
        text = body.decode("utf-8-sig") if isinstance(body, bytes) else str(body)
        payload = json.loads(
            text,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=EastmoneyQuoteProvider._reject_json_constant,
        )
        if not isinstance(payload, Mapping):
            raise ValueError("eastmoney us response must be an object")
        data = payload.get("data")
        if not isinstance(data, Mapping):
            return []
        rows = data.get("diff", [])
        if not isinstance(rows, list):
            raise ValueError("eastmoney us data.diff must be a list")
        return [row for row in rows if isinstance(row, dict)]

    def _snapshot_from_row(
        self,
        row: Mapping[str, object],
        *,
        symbol: str,
        fetched_at: datetime,
    ) -> QuoteSnapshot | None:
        """将美股上游记录转换为精确快照；盘前/盘后优先采信有效扩展成交价并标注时段。"""
        try:
            regular_price = EastmoneyQuoteProvider._optional_decimal(
                row.get("f2"), field_name="last_price"
            )
            prev_close = EastmoneyQuoteProvider._optional_decimal(
                row.get("f18"), field_name="prev_close"
            )
            ext_price = EastmoneyQuoteProvider._optional_decimal(
                row.get("f251"), field_name="ext_price"
            )
        except (TypeError, ValueError):
            return None
        if prev_close is not None and prev_close <= 0:
            return None
        try:
            regular_change = EastmoneyQuoteProvider._optional_decimal(
                row.get("f3"), field_name="change_pct"
            )
        except (TypeError, ValueError):
            regular_change = None
        try:
            ext_change = EastmoneyQuoteProvider._optional_decimal(
                row.get("f253"), field_name="ext_change_pct"
            )
        except (TypeError, ValueError):
            ext_change = None

        source_time = EastmoneyQuoteProvider._source_time(row.get("f124"))
        current_phase = self._calendar.session_phase(fetched_at)
        if current_phase in {"pre", "post"} and ext_price is not None and ext_price > 0:
            # 盘前基准为昨收，盘后基准优先为当日常规收盘价（回退昨收）。
            reference_close = regular_price if current_phase == "post" and regular_price and regular_price > 0 else prev_close
            change_pct = ext_change
            if change_pct is None and reference_close is not None and reference_close > 0:
                change_pct = round_price((ext_price - reference_close) / reference_close * Decimal("100"))
            return QuoteSnapshot(
                symbol=symbol,
                last_price=ext_price,
                prev_close=reference_close,
                change_pct=change_pct,
                source_time=source_time or fetched_at,
                fetched_at=fetched_at,
                provider=self.name,
                market_session=current_phase,
            )

        if regular_price is None or regular_price <= 0:
            return None
        resolved_phase = self._calendar.session_phase(source_time or fetched_at)
        return QuoteSnapshot(
            symbol=symbol,
            last_price=regular_price,
            prev_close=prev_close,
            change_pct=regular_change,
            source_time=source_time,
            fetched_at=fetched_at,
            provider=self.name,
            market_session=resolved_phase,
        )

    def _build_secids_for_batch(self, batch: Sequence[str]) -> tuple[list[str], dict[str, str]]:
        """为一批美股代码构造 secid 列表，未命中市场码时回退探测 105/106/107。"""
        secids: list[str] = []
        canonical_by_code: dict[str, str] = {}
        for canonical in batch:
            _, code = canonical.split(":", 1)
            canonical_by_code[code] = canonical
            upstream_market = self._resolver.resolve(
                code,
                transport=self._transport,
                timeout_seconds=float(self.timeout_seconds),
            )
            if upstream_market is not None:
                secids.append(f"{upstream_market}.{code}")
            else:
                for candidate_market in _US_UPSTREAM_MARKETS:
                    secids.append(f"{candidate_market}.{code}")
        return secids, canonical_by_code

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        """批量获取美股常规及盘前/盘后行情快照。"""
        requested: list[str] = []
        seen: set[str] = set()
        for value in symbols:
            canonical = canonical_symbol(value)
            if canonical in seen or not self.supports_symbol(canonical):
                continue
            seen.add(canonical)
            requested.append(canonical)
        if not requested:
            return {}
        if len(requested) > self.max_symbols:
            raise ValueError("eastmoney us quote request exceeds cumulative symbol limit")

        result: dict[str, QuoteSnapshot] = {}
        batch_error: Exception | None = None
        for offset in range(0, len(requested), self.max_batch_size):
            batch = requested[offset : offset + self.max_batch_size]
            secids, canonical_by_code = self._build_secids_for_batch(batch)
            for sub_offset in range(0, len(secids), self.max_batch_size):
                sub_secids = secids[sub_offset : sub_offset + self.max_batch_size]
                try:
                    rows = self._request_secids(sub_secids)
                except Exception as exc:
                    batch_error = exc
                    continue
                fetched_at = self._now()
                if fetched_at.tzinfo is None:
                    fetched_at = fetched_at.replace(tzinfo=UTC)
                for row in rows:
                    row_code = str(row.get("f12") or "").strip().upper()
                    row_market = str(row.get("f13") or "").strip()
                    resolved_symbol = canonical_by_code.get(row_code)
                    if resolved_symbol is None or row_market not in _US_UPSTREAM_MARKETS:
                        continue
                    snapshot = self._snapshot_from_row(
                        row, symbol=resolved_symbol, fetched_at=fetched_at
                    )
                    if snapshot is not None:
                        self._resolver.remember(row_code, row_market)
                        result[resolved_symbol] = snapshot
        if batch_error is not None and not result:
            raise batch_error
        return result

    def health(self) -> ProviderHealth:
        """报告美股行情源已完成配置，不在健康检查时访问公网。"""
        return ProviderHealth(
            provider=self.name,
            healthy=True,
            message="美股行情源已配置，连通性在刷新时检查",
        )


class TencentUSQuoteProvider:
    """腾讯财经美股/美股 ETF 批量公开行情源（无需交易所编号，支持 VOO/SPY 等全量美股）。"""

    name = "tencent-us"
    supported_markets = frozenset({"US"})
    _endpoint = "https://qt.gtimg.cn/q="
    _max_response_bytes = 2 * 1024 * 1024

    def __init__(
        self,
        *,
        timeout_seconds: Decimal | str | int | float = Decimal("5"),
        transport: Callable[[Request, float], bytes] | None = None,
        now: Callable[[], datetime] | None = None,
        calendar: USTradingCalendar | None = None,
        max_batch_size: int = 60,
        max_symbols: int = 500,
    ) -> None:
        """初始化腾讯美股批量行情源的超时、批量上限与可注入传输。"""
        timeout = (
            Decimal(str(timeout_seconds))
            if isinstance(timeout_seconds, float)
            else ensure_decimal(timeout_seconds, field="timeout_seconds")
        )
        if not timeout.is_finite() or timeout <= 0:
            raise ValueError("timeout_seconds must be a finite positive number")
        if max_batch_size <= 0 or max_batch_size > 100:
            raise ValueError("max_batch_size must be between 1 and 100")
        if max_symbols <= 0:
            raise ValueError("max_symbols must be positive")
        self.timeout_seconds = timeout
        self._transport = transport or EastmoneyQuoteProvider()._default_transport
        self._now = now or _now
        self._calendar = calendar or USTradingCalendar()
        self.max_batch_size = max_batch_size
        self.max_symbols = max_symbols

    def supports_symbol(self, symbol: str) -> bool:
        """判断证券是否属于美股市场且代码格式合法。"""
        canonical = canonical_symbol(symbol)
        if ":" not in canonical:
            return False
        market, code = canonical.split(":", 1)
        return market in self.supported_markets and bool(_US_SYMBOL_PATTERN.match(code))

    @staticmethod
    def _parse_source_time(value: str) -> datetime | None:
        """将腾讯美股返回的美东时间字符串（YYYY-MM-DD HH:MM:SS）转换为 UTC。"""
        raw = value.strip()
        if not raw:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(raw[:19], fmt)
                return parsed.replace(tzinfo=USTradingCalendar.timezone).astimezone(UTC)
            except ValueError:
                continue
        return None

    def _parse_line(
        self,
        line: str,
        *,
        canonical_by_code: Mapping[str, str],
        fetched_at: datetime,
    ) -> tuple[str, QuoteSnapshot] | None:
        """解析单行腾讯美股报价（v_usVOO=\"200~...\"）为精确 Decimal 快照。"""
        if "=" not in line or "~" not in line:
            return None
        left, right = line.split("=", 1)
        prefix = left.strip()
        if not prefix.startswith("v_us"):
            return None
        raw_code = prefix[4:].strip().upper()
        canonical = canonical_by_code.get(raw_code)
        if canonical is None:
            return None
        payload = right.strip().rstrip(";").strip('"')
        parts = payload.split("~")
        if len(parts) < 33:
            return None
        try:
            last_price = EastmoneyQuoteProvider._optional_decimal(parts[3], field_name="last_price")
            prev_close = EastmoneyQuoteProvider._optional_decimal(parts[4], field_name="prev_close")
            if last_price is None or last_price <= 0:
                return None
            if prev_close is not None and prev_close <= 0:
                prev_close = None
        except (TypeError, ValueError):
            return None
        try:
            change_pct = EastmoneyQuoteProvider._optional_decimal(parts[32], field_name="change_pct")
        except (TypeError, ValueError):
            change_pct = None
        source_time = self._parse_source_time(parts[30])
        market_session = self._calendar.session_phase(source_time or fetched_at)
        return canonical, QuoteSnapshot(
            symbol=canonical,
            last_price=last_price,
            prev_close=prev_close,
            change_pct=change_pct,
            source_time=source_time,
            fetched_at=fetched_at,
            provider=self.name,
            market_session=market_session,
        )

    def _request_batch(self, batch: Sequence[str]) -> str:
        """批量请求腾讯美股行情文本并按 GBK 解码。"""
        codes = [f"us{canonical.split(':', 1)[1]}" for canonical in batch]
        request = Request(
            f"{self._endpoint}{','.join(codes)}",
            headers={
                "Accept": "*/*",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://gu.qq.com/",
            },
        )
        body = self._transport(request, float(self.timeout_seconds))
        if len(body) > self._max_response_bytes:
            raise ValueError("tencent us response exceeds size limit")
        return body.decode("gbk", errors="ignore") if isinstance(body, bytes) else str(body)

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        """批量获取美股及美股 ETF 实时行情快照。"""
        requested: list[str] = []
        seen: set[str] = set()
        for value in symbols:
            canonical = canonical_symbol(value)
            if canonical in seen or not self.supports_symbol(canonical):
                continue
            seen.add(canonical)
            requested.append(canonical)
        if not requested:
            return {}
        if len(requested) > self.max_symbols:
            raise ValueError("tencent us quote request exceeds cumulative symbol limit")

        result: dict[str, QuoteSnapshot] = {}
        batch_error: Exception | None = None
        for offset in range(0, len(requested), self.max_batch_size):
            batch = requested[offset : offset + self.max_batch_size]
            try:
                text = self._request_batch(batch)
            except Exception as exc:
                batch_error = exc
                continue
            fetched_at = self._now()
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=UTC)
            canonical_by_code = {canonical.split(":", 1)[1]: canonical for canonical in batch}
            for line in text.splitlines():
                parsed = self._parse_line(
                    line,
                    canonical_by_code=canonical_by_code,
                    fetched_at=fetched_at,
                )
                if parsed is not None:
                    result[parsed[0]] = parsed[1]
        if batch_error is not None and not result:
            raise batch_error
        return result

    def health(self) -> ProviderHealth:
        """报告腾讯美股行情源已完成配置，不在健康检查时访问公网。"""
        return ProviderHealth(
            provider=self.name,
            healthy=True,
            message="腾讯美股行情源已配置，连通性在刷新时检查",
        )


class FallbackUSQuoteProvider:
    """美股多源容错适配器：优先腾讯批量行情，缺失或失败时回退东方财富美股源。"""

    name = "us-fallback"

    def __init__(
        self,
        primary: QuoteProvider,
        secondary: QuoteProvider,
    ) -> None:
        """保存主/备美股行情源。"""
        self.primary = primary
        self.secondary = secondary

    def supports_symbol(self, symbol: str) -> bool:
        """主源或备源任一支持该美股代码即返回 True。"""
        return CompositeQuoteProvider._supports(self.primary, symbol) or CompositeQuoteProvider._supports(
            self.secondary, symbol
        )

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        """优先从主源批量获取，并对缺失标的自动回退备源补齐。"""
        requested = [canonical_symbol(symbol) for symbol in symbols if self.supports_symbol(symbol)]
        if not requested:
            return {}
        result: dict[str, QuoteSnapshot] = {}
        primary_error: Exception | None = None
        try:
            result.update(self.primary.get_quotes(requested))
        except Exception as exc:
            primary_error = exc
        missing = [symbol for symbol in requested if symbol not in result]
        if missing:
            try:
                result.update(self.secondary.get_quotes(missing))
            except Exception as exc:
                if not result:
                    raise (primary_error or exc)
        return result

    def health(self) -> ProviderHealth:
        """主源健康即返回健康状态，否则回退备源健康检查。"""
        primary_health = self.primary.health()
        if primary_health.healthy:
            return primary_health
        return self.secondary.health()


class EastmoneyFundQuoteProvider:
    """东方财富/天天基金场外开放式基金已公布单位净值行情源。"""

    name = "eastmoney-fund"
    supported_markets = frozenset({"OF"})
    _endpoint = "https://api.fund.eastmoney.com/f10/lsjz"
    _max_response_bytes = 2 * 1024 * 1024

    def __init__(
        self,
        *,
        timeout_seconds: Decimal | str | int | float = Decimal("5"),
        transport: Callable[[Request, float], bytes] | None = None,
        now: Callable[[], datetime] | None = None,
        max_symbols: int = 200,
    ) -> None:
        """校验超时和请求上限并保存可注入传输。"""
        timeout = (
            Decimal(str(timeout_seconds))
            if isinstance(timeout_seconds, float)
            else ensure_decimal(timeout_seconds, field="timeout_seconds")
        )
        if not timeout.is_finite() or timeout <= 0:
            raise ValueError("timeout_seconds must be a finite positive number")
        if max_symbols <= 0:
            raise ValueError("max_symbols must be positive")
        self.timeout_seconds = timeout
        self._transport = transport or EastmoneyQuoteProvider()._default_transport
        self._now = now or _now
        self.max_symbols = max_symbols

    def supports_symbol(self, symbol: str) -> bool:
        """判断证券是否属于场外开放式基金（OF:6位数字代码）。"""
        canonical = canonical_symbol(symbol)
        if ":" not in canonical:
            return False
        market, code = canonical.split(":", 1)
        return market in self.supported_markets and len(code) == 6 and code.isdigit()

    @staticmethod
    def _nav_source_time(value: object) -> datetime | None:
        """将基金净值日期（YYYY-MM-DD）转换为上海时区收盘 15:00 对应的 UTC 时间。"""
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            nav_date = datetime.strptime(raw[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
        return datetime.combine(
            nav_date, time(15, 0), tzinfo=AShareTradingCalendar.timezone
        ).astimezone(UTC)

    def _fetch_nav_rows(self, code: str, *, page_size: int = 2) -> list[dict[str, object]]:
        """请求单只基金最近已公布单位净值记录，使用严格 Decimal 解析。"""
        query = urlencode(
            {
                "fundCode": code,
                "pageIndex": "1",
                "pageSize": str(page_size),
            }
        )
        request = Request(
            f"{self._endpoint}?{query}",
            headers={
                "Accept": "application/json",
                "User-Agent": "wing-trade/0.1 (+backend market data)",
                "Referer": "https://fundf10.eastmoney.com/",
            },
        )
        body = self._transport(request, float(self.timeout_seconds))
        if len(body) > self._max_response_bytes:
            raise ValueError("eastmoney fund response exceeds size limit")
        text = body.decode("utf-8-sig") if isinstance(body, bytes) else str(body)
        payload = json.loads(
            text,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=EastmoneyQuoteProvider._reject_json_constant,
        )
        if not isinstance(payload, Mapping):
            raise ValueError("eastmoney fund response must be an object")
        data = payload.get("Data")
        if not isinstance(data, Mapping):
            return []
        rows = data.get("LSJZList")
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]

    def _snapshot_from_rows(
        self,
        rows: Sequence[Mapping[str, object]],
        *,
        symbol: str,
        fetched_at: datetime,
    ) -> QuoteSnapshot | None:
        """从按日期倒序的净值列表中提取最新已公布单位净值与日涨跌幅。"""
        valid_rows: list[tuple[Decimal, datetime | None, Decimal | None]] = []
        for row in rows:
            try:
                nav = EastmoneyQuoteProvider._optional_decimal(row.get("DWJZ"), field_name="last_price")
            except (TypeError, ValueError):
                continue
            if nav is None or nav <= 0:
                continue
            try:
                change_pct = EastmoneyQuoteProvider._optional_decimal(
                    row.get("JZZZL"), field_name="change_pct"
                )
            except (TypeError, ValueError):
                change_pct = None
            valid_rows.append((nav, self._nav_source_time(row.get("FSRQ")), change_pct))
            if len(valid_rows) >= 2:
                break
        if not valid_rows:
            return None
        latest_nav, source_time, change_pct = valid_rows[0]
        prev_close = valid_rows[1][0] if len(valid_rows) > 1 else None
        return QuoteSnapshot(
            symbol=symbol,
            last_price=latest_nav,
            prev_close=prev_close,
            change_pct=change_pct,
            source_time=source_time,
            fetched_at=fetched_at,
            provider=self.name,
        )

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        """逐只读取场外基金最新已公布单位净值，单只失败不阻塞其余基金。"""
        requested: list[str] = []
        seen: set[str] = set()
        for value in symbols:
            canonical = canonical_symbol(value)
            if canonical in seen or not self.supports_symbol(canonical):
                continue
            seen.add(canonical)
            requested.append(canonical)
        if not requested:
            return {}
        if len(requested) > self.max_symbols:
            raise ValueError("eastmoney fund quote request exceeds cumulative symbol limit")

        result: dict[str, QuoteSnapshot] = {}
        last_error: Exception | None = None
        for canonical in requested:
            _, code = canonical.split(":", 1)
            try:
                rows = self._fetch_nav_rows(code, page_size=2)
            except Exception as exc:
                last_error = exc
                continue
            fetched_at = self._now()
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=UTC)
            snapshot = self._snapshot_from_rows(rows, symbol=canonical, fetched_at=fetched_at)
            if snapshot is not None:
                result[canonical] = snapshot
        if last_error is not None and not result:
            raise last_error
        return result

    def health(self) -> ProviderHealth:
        """报告基金净值行情源已完成配置，不在健康检查时访问公网。"""
        return ProviderHealth(
            provider=self.name,
            healthy=True,
            message="基金净值源已配置，连通性在刷新时检查",
        )


class CompositeQuoteProvider:
    """按证券市场路由到对应行情源的组合适配器。"""

    def __init__(self, providers: Sequence[QuoteProvider], *, name: str = "eastmoney") -> None:
        """保存有序子行情源列表，供按标的路由请求。"""
        if not providers:
            raise ValueError("providers must not be empty")
        self.providers = tuple(providers)
        self.name = name

    @staticmethod
    def _supports(provider: QuoteProvider, symbol: str) -> bool:
        """判断子行情源是否支持目标证券。"""
        supports = getattr(provider, "supports_symbol", None)
        return True if supports is None else bool(supports(symbol))

    def supports_symbol(self, symbol: str) -> bool:
        """只要任一子行情源支持该证券即返回 True。"""
        return any(self._supports(provider, symbol) for provider in self.providers)

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        """按子行情源分组获取行情并合并，部分子源失败不丢弃其余成功结果。"""
        result: dict[str, QuoteSnapshot] = {}
        last_error: Exception | None = None
        for provider in self.providers:
            subset = [symbol for symbol in symbols if self._supports(provider, symbol)]
            if not subset:
                continue
            try:
                result.update(provider.get_quotes(subset))
            except Exception as exc:
                last_error = exc
        if last_error is not None and not result:
            raise last_error
        return result

    def health(self) -> ProviderHealth:
        """汇总所有子行情源健康状态，不发起公网请求。"""
        statuses = [provider.health() for provider in self.providers]
        healthy = all(item.healthy for item in statuses)
        messages = [item.message for item in statuses if item.message]
        return ProviderHealth(
            provider=self.name,
            healthy=healthy,
            message="; ".join(messages) if messages else None,
        )

