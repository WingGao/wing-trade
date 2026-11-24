from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Literal
from urllib.parse import urlencode
from urllib.request import Request

from app.domain.precision import ensure_decimal, round_price

from .calendar import AShareTradingCalendar, OpenFundCalendar, USTradingCalendar
from .provider import (
    EastmoneyFundQuoteProvider,
    EastmoneyQuoteProvider,
    QuoteSnapshot,
    USMarketResolver,
    canonical_symbol,
    shared_us_market_resolver,
)


@dataclass(frozen=True, slots=True)
class SecurityCandidate:
    """外部查名返回的证券信息，不隐式创建本地证券。"""

    market: str
    symbol: str
    name: str
    security_type: str = "STOCK"


@dataclass(frozen=True, slots=True)
class IntradayPoint:
    """来自行情源的真实价格或单位净值时序点。"""

    time: datetime
    price: Decimal


@dataclass(frozen=True, slots=True)
class IntradaySnapshot:
    """同一次读取的当日分时、估算分时或近期净值曲线与最新行情，失败时可保留真实缓存。"""

    symbol: str
    trading_date: str
    points: tuple[IntradayPoint, ...]
    quote: QuoteSnapshot | None
    fetched_at: datetime
    provider: str = "eastmoney"
    is_stale: bool = False
    error: str | None = None
    series_kind: Literal["intraday", "estimated_intraday", "nav"] = "intraday"


class SecurityInfoProvider:
    """使用东方财富公开接口查询证券名称、当日分钟时序和场外基金近期净值。"""

    _search_url = "https://searchapi.eastmoney.com/api/suggest/get"
    _trends_url = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
    _fund_nav_url = "https://api.fund.eastmoney.com/f10/lsjz"
    _tencent_us_minute_url = "https://web.ifzq.gtimg.cn/appstock/app/usMinute/query"
    _max_response_bytes = 2 * 1024 * 1024

    def __init__(
        self,
        *,
        transport: Callable[[Request, float], bytes] | None = None,
        now: Callable[[], datetime] | None = None,
        timeout_seconds: int = 5,
        us_resolver: USMarketResolver | None = None,
    ) -> None:
        """复用已有有界 HTTP 传输，允许离线注入数据源、时钟与美股市场码解析器。"""
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._transport = transport or EastmoneyQuoteProvider()._default_transport
        self._now = now or (lambda: datetime.now(UTC))
        self.timeout_seconds = timeout_seconds
        self.calendar = AShareTradingCalendar()
        self.us_calendar = USTradingCalendar()
        self.fund_calendar = OpenFundCalendar()
        self._us_markets: OrderedDict[str, str] = OrderedDict()
        self._us_resolver = (
            us_resolver
            if us_resolver is not None
            else (
                USMarketResolver(transport=transport, timeout_seconds=timeout_seconds)
                if transport is not None
                else shared_us_market_resolver()
            )
        )

    def now(self) -> datetime:
        """返回规范 UTC 时间，保证缓存和日期判断使用同一时钟。"""
        value = self._now()
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def calendar_for_symbol(self, symbol: str) -> AShareTradingCalendar:
        """根据证券市场选择交易日，避免美股跨北京时间午夜时切断曲线。"""
        canonical = canonical_symbol(symbol)
        if canonical.startswith("US:"):
            return self.us_calendar
        if canonical.startswith("OF:"):
            return self.fund_calendar
        return self.calendar

    def _request(
        self,
        endpoint: str,
        params: Mapping[str, str],
        *,
        referer: str = "https://quote.eastmoney.com/",
    ) -> Mapping[str, object]:
        """有界读取 JSON 并拒绝非有限数值，不引入二进制浮点价格。"""
        request = Request(
            f"{endpoint}?{urlencode(params)}",
            headers={
                "Accept": "application/json",
                "User-Agent": "wing-trade/0.1 (+backend market data)",
                "Referer": referer,
            },
        )
        body = self._transport(request, float(self.timeout_seconds))
        if len(body) > self._max_response_bytes:
            raise ValueError("行情响应超过大小限制")
        payload = json.loads(
            body.decode("utf-8-sig"),
            parse_float=Decimal,
            parse_constant=EastmoneyQuoteProvider._reject_json_constant,
        )
        if not isinstance(payload, Mapping):
            raise ValueError("行情响应不是 JSON 对象")
        return payload

    def search(self, keyword: str) -> list[SecurityCandidate]:
        """按代码或名称访问外部查名接口，保留完整市场身份并去重。"""
        keyword = keyword.strip()
        if not keyword:
            return []
        if len(keyword) > 80:
            raise ValueError("证券查询最多 80 个字符")
        requested_market = ""
        if ":" in keyword:
            prefix, suffix = keyword.split(":", 1)
            if prefix.upper() in {"SH", "SZ", "BJ", "US", "OF"}:
                requested_market = prefix.upper()
                keyword = suffix.split()[0] if suffix.strip() else ""
        payload = self._request(
            self._search_url,
            {
                "input": keyword,
                "type": "14",
                "token": "D43BF722C8E33BDC906FB84D85E326E8",
                "count": "20",
            },
        )
        quotation = payload.get("QuotationCodeTable")
        if not isinstance(quotation, Mapping):
            raise ValueError("外部查名响应缺少证券列表")
        rows = quotation.get("Data")
        if rows is None:
            return []
        if not isinstance(rows, list):
            raise ValueError("外部查名证券列表格式无效")
        result: dict[tuple[str, str], SecurityCandidate] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            code, name = str(row.get("Code") or "").strip(), str(row.get("Name") or "").strip()
            if not code or not name:
                continue
            upstream_market = str(row.get("MktNum", row.get("MarketType", "")))
            classify = str(row.get("Classify") or "").upper()
            security_type_name = str(row.get("SecurityTypeName") or "")
            quote_id = str(row.get("QuoteID") or "")
            security_type = "STOCK"
            if code.isdigit() and len(code) == 6 and upstream_market in {"0", "1"}:
                market = "SH" if upstream_market == "1" else "BJ" if code[0] in "489" else "SZ"
                if "指数" in security_type_name or classify in {"INDEX", "INDEXSTOCK"}:
                    security_type = "INDEX"
            elif upstream_market in {"105", "106", "107"} or classify in {"US", "USSTOCK"}:
                market = "US"
            elif (
                code.isdigit()
                and len(code) == 6
                and (
                    classify in {"FUND", "OPENFUND"}
                    or "基金" in security_type_name
                    or quote_id.startswith("7.")
                )
            ):
                market = "OF"
                security_type = "FUND"
            else:
                continue
            if requested_market and market != requested_market:
                continue
            code = code.upper()
            if market == "US":
                self._us_markets[code] = upstream_market
                self._us_markets.move_to_end(code)
                while len(self._us_markets) > 128:
                    self._us_markets.popitem(last=False)
                self._us_resolver.remember(code, upstream_market)
            result[(market, code)] = SecurityCandidate(market, code, name, security_type)
        return list(result.values())

    def fund_nav_history(self, symbol: str, *, days: int = 30) -> IntradaySnapshot:
        """读取场外开放式基金近期已公布单位净值曲线及最新净值快照。"""
        canonical = canonical_symbol(symbol)
        if not EastmoneyFundQuoteProvider().supports_symbol(canonical):
            raise ValueError("基金净值查询仅支持 OF:6位数字代码")
        _, code = canonical.split(":", 1)
        now = self.now()
        payload = self._request(
            self._fund_nav_url,
            {
                "fundCode": code,
                "pageIndex": "1",
                "pageSize": str(days),
            },
            referer="https://fundf10.eastmoney.com/",
        )
        data = payload.get("Data")
        if not isinstance(data, Mapping):
            raise ValueError("暂无基金已公布净值数据")
        rows = data.get("LSJZList")
        if not isinstance(rows, list):
            raise ValueError("基金净值列表格式无效")
        parsed: dict[datetime, tuple[str, Decimal, Decimal | None]] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            raw_date = str(row.get("FSRQ") or "").strip()[:10]
            if not raw_date:
                continue
            try:
                nav_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
                nav = round_price(ensure_decimal(row.get("DWJZ"), field="fund.nav"))
            except (TypeError, ValueError):
                continue
            if nav <= 0:
                continue
            try:
                change_pct = EastmoneyQuoteProvider._optional_decimal(
                    row.get("JZZZL"), field_name="change_pct"
                )
            except (TypeError, ValueError):
                change_pct = None
            point_time = datetime.combine(
                nav_date, time(15, 0), tzinfo=self.calendar.timezone
            ).astimezone(UTC)
            parsed[point_time] = (nav_date.isoformat(), nav, change_pct)
        if not parsed:
            raise ValueError("暂无基金已公布净值数据")
        ordered_times = sorted(parsed)
        points = tuple(IntradayPoint(point_time, parsed[point_time][1]) for point_time in ordered_times)
        latest_time = ordered_times[-1]
        latest_date, latest_nav, latest_change = parsed[latest_time]
        prev_close = parsed[ordered_times[-2]][1] if len(ordered_times) >= 2 else None
        quote = QuoteSnapshot(
            symbol=canonical,
            last_price=latest_nav,
            prev_close=prev_close,
            change_pct=latest_change,
            source_time=latest_time,
            fetched_at=now,
            provider="eastmoney-fund",
            is_stale=False,
        )
        return IntradaySnapshot(
            symbol=canonical,
            trading_date=latest_date,
            points=points,
            quote=quote,
            fetched_at=now,
            provider="eastmoney-fund",
            is_stale=False,
            error=None,
            series_kind="nav",
        )

    def _us_intraday_tencent(self, canonical: str, code: str) -> IntradaySnapshot:
        """从腾讯美股分时接口读取当日真实分钟序列与最新报价，作为东财断连时的稳定回退。"""
        now = self.now()
        calendar = self.us_calendar
        today = now.astimezone(calendar.timezone).date()
        payload = self._request(
            self._tencent_us_minute_url,
            {"code": f"us{code}"},
            referer="https://gu.qq.com/",
        )
        data_map = payload.get("data")
        if not isinstance(data_map, Mapping):
            raise ValueError("腾讯美股分时响应缺少 data 字段")
        entry = data_map.get(f"us{code}")
        if not isinstance(entry, Mapping):
            raise ValueError("今日暂无该美股分时行情")
        minute_block = entry.get("data")
        if not isinstance(minute_block, Mapping):
            raise ValueError("腾讯美股分时格式无效")
        raw_date = str(minute_block.get("date") or "").strip()
        try:
            series_date = datetime.strptime(raw_date[:8], "%Y%m%d").date()
        except ValueError as exc:
            raise ValueError("腾讯美股分时缺少有效交易日期") from exc
        if series_date != today:
            raise ValueError("今日暂无日内行情，未将历史交易日伪装为今日")
        rows = minute_block.get("data")
        if not isinstance(rows, list):
            raise ValueError("腾讯美股分时点列表无效")
        by_time: dict[datetime, IntradayPoint] = {}
        for row in rows:
            if not isinstance(row, str):
                continue
            parts = row.split()
            if len(parts) < 2 or len(parts[0]) != 4 or not parts[0].isdigit():
                continue
            try:
                hour = int(parts[0][:2])
                minute = int(parts[0][2:4])
                point_local = datetime.combine(
                    series_date, time(hour, minute), tzinfo=calendar.timezone
                )
                price = round_price(ensure_decimal(parts[1], field="intraday.price"))
            except (TypeError, ValueError):
                continue
            if price <= 0 or point_local > now.astimezone(calendar.timezone):
                continue
            utc_time = point_local.astimezone(UTC)
            by_time[utc_time] = IntradayPoint(utc_time, price)
        points = tuple(by_time[t] for t in sorted(by_time))
        if not points:
            raise ValueError("今日暂无日内行情，未将历史交易日伪装为今日")
        prev_close: Decimal | None = None
        qt_block = entry.get("qt")
        if isinstance(qt_block, Mapping):
            qt_row = qt_block.get(f"us{code}")
            if isinstance(qt_row, list) and len(qt_row) > 4:
                try:
                    parsed_prev = EastmoneyQuoteProvider._optional_decimal(
                        qt_row[4], field_name="prev_close"
                    )
                    if parsed_prev is not None and parsed_prev > 0:
                        prev_close = parsed_prev
                except (TypeError, ValueError):
                    prev_close = None
        stale = calendar.is_session_open(now) and now - points[-1].time > timedelta(minutes=2)
        quote = QuoteSnapshot(
            symbol=canonical,
            last_price=points[-1].price,
            prev_close=prev_close,
            source_time=points[-1].time,
            fetched_at=now,
            provider="tencent-us-intraday",
            is_stale=stale,
            market_session=calendar.session_phase(points[-1].time),
        )
        return IntradaySnapshot(
            symbol=canonical,
            trading_date=today.isoformat(),
            points=points,
            quote=quote,
            fetched_at=now,
            provider="tencent-us-intraday",
            is_stale=stale,
            error="行情源暂未更新，显示当日历史参考" if stale else None,
        )

    def intraday(self, symbol: str) -> IntradaySnapshot:
        """读取单日真实分钟序列，场外基金返回近期净值曲线，美股东财失败时自动回退腾讯分时。"""
        canonical = canonical_symbol(symbol)
        if ":" not in canonical:
            raise ValueError("日内行情需要完整市场代码")
        market, code = canonical.split(":", 1)
        if market == "OF":
            return self.fund_nav_history(canonical)
        if market == "US":
            try:
                return self._intraday_eastmoney(canonical, market, code)
            except Exception:
                return self._us_intraday_tencent(canonical, code)
        return self._intraday_eastmoney(canonical, market, code)

    def _intraday_eastmoney(self, canonical: str, market: str, code: str) -> IntradaySnapshot:
        """通过东方财富分时接口读取沪深北或美股当日分钟序列。"""
        if market == "US":
            upstream_market = self._us_markets.get(code) or self._us_resolver.get_cached(code)
            if upstream_market is None:
                self.search(canonical)
                upstream_market = self._us_markets.get(code) or self._us_resolver.resolve(
                    code,
                    transport=self._transport,
                    timeout_seconds=float(self.timeout_seconds),
                )
            if upstream_market is None:
                raise ValueError("未能查到该美股对应的行情市场")
            self._us_markets[code] = upstream_market
            self._us_resolver.remember(code, upstream_market)
        elif EastmoneyQuoteProvider().supports_symbol(canonical):
            upstream_market = "1" if market == "SH" else "0"
        else:
            raise ValueError("当前日内行情源仅支持沪深北和美股证券")
        now = self.now()
        calendar = self.calendar_for_symbol(canonical)
        today = now.astimezone(calendar.timezone).date()
        payload = self._request(
            self._trends_url,
            {
                "secid": f"{upstream_market}.{code}",
                "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
                "ndays": "1",
                "iscr": "1" if market == "US" else "0",
            },
        )
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise ValueError("今日暂无日内行情")
        if str(data.get("code", "")) != code:
            raise ValueError("日内行情证券代码与请求不一致")
        if str(data.get("market", "")) != upstream_market:
            raise ValueError("日内行情市场与请求不一致")
        rows = data.get("trends")
        if not isinstance(rows, list):
            raise ValueError("日内行情序列格式无效")
        by_time: dict[datetime, IntradayPoint] = {}
        for row in rows:
            if not isinstance(row, str):
                continue
            parts = row.split(",")
            if len(parts) < 3:
                continue
            try:
                time = datetime.strptime(parts[0], "%Y-%m-%d %H:%M").replace(
                    tzinfo=self.calendar.timezone
                )
                price = round_price(ensure_decimal(parts[2], field="intraday.price"))
            except (TypeError, ValueError):
                continue
            if (
                price <= 0
                or time.astimezone(calendar.timezone).date() != today
                or time > now.astimezone(time.tzinfo)
            ):
                continue
            utc_time = time.astimezone(UTC)
            by_time[utc_time] = IntradayPoint(utc_time, price)
        points = tuple(by_time[time] for time in sorted(by_time))
        if not points:
            raise ValueError("今日暂无日内行情，未将历史交易日伪装为今日")
        previous = data.get("preClose", data.get("prePrice"))
        try:
            prev_close = ensure_decimal(previous, field="prev_close") if previous else None
            if prev_close is not None and prev_close <= 0:
                prev_close = None
        except (TypeError, ValueError):
            prev_close = None
        stale = calendar.is_session_open(now) and now - points[-1].time > timedelta(minutes=2)
        quote = QuoteSnapshot(
            symbol=canonical,
            last_price=points[-1].price,
            prev_close=prev_close,
            source_time=points[-1].time,
            fetched_at=now,
            provider="eastmoney-intraday",
            is_stale=stale,
            market_session=calendar.session_phase(points[-1].time),
        )
        return IntradaySnapshot(
            symbol=canonical,
            trading_date=today.isoformat(),
            points=points,
            quote=quote,
            fetched_at=now,
            is_stale=stale,
            error="行情源暂未更新，显示当日历史参考" if stale else None,
        )


class IntradayService:
    """限制外部请求并保存有界同日真实行情缓存，刷新失败保留参考数据。"""

    def __init__(self, provider: SecurityInfoProvider, *, max_cached: int = 128) -> None:
        """初始化缓存与串行锁，避免三秒轮询重叠访问上游。"""
        if max_cached <= 0:
            raise ValueError("max_cached must be positive")
        self.provider = provider
        self.max_cached = max_cached
        self._cache: OrderedDict[str, IntradaySnapshot] = OrderedDict()
        self._attempted_at: dict[str, datetime] = {}
        self._lock = asyncio.Lock()

    def _remember(self, symbol: str, snapshot: IntradaySnapshot, at: datetime) -> IntradaySnapshot:
        """同时限制成功与失败响应的请求频率，保留原始报价获取时间。"""
        self._cache[symbol] = snapshot
        self._attempted_at[symbol] = at
        self._cache.move_to_end(symbol)
        while len(self._cache) > self.max_cached:
            oldest, _ = self._cache.popitem(last=False)
            self._attempted_at.pop(oldest, None)
        return snapshot

    async def read(self, symbol: str) -> IntradaySnapshot:
        """读取最新当日行情或基金近期净值，异常降级为明确过期的缓存或空状态。"""
        canonical = canonical_symbol(symbol)
        is_fund = canonical.startswith("OF:")
        min_interval = timedelta(minutes=5) if is_fund else timedelta(seconds=2.5)
        async with self._lock:
            now = self.provider.now()
            calendar = self.provider.calendar_for_symbol(canonical)
            today = now.astimezone(calendar.timezone).date().isoformat()
            cached = self._cache.get(canonical)
            if not is_fund and cached is not None and cached.trading_date != today:
                self._cache.pop(canonical, None)
                self._attempted_at.pop(canonical, None)
                cached = None
            if cached is not None and timedelta(0) <= now - cached.fetched_at < min_interval:
                self._cache.move_to_end(canonical)
                return cached
            attempted_at = self._attempted_at.get(canonical)
            if (
                cached is not None
                and attempted_at is not None
                and timedelta(0) <= now - attempted_at < min_interval
            ):
                self._cache.move_to_end(canonical)
                return cached
            try:
                snapshot = await asyncio.to_thread(self.provider.intraday, canonical)
            except Exception as exc:
                error = str(exc) or ("近期净值暂时不可用" if is_fund else "日内行情暂时不可用")
                if cached is not None:
                    return self._remember(
                        canonical,
                        replace(
                            cached,
                            is_stale=True,
                            quote=cached.quote.stale() if cached.quote else None,
                            error=error,
                        ),
                        now,
                    )
                return self._remember(
                    canonical,
                    IntradaySnapshot(
                        symbol=canonical,
                        trading_date=today,
                        points=(),
                        quote=None,
                        fetched_at=now,
                        provider="eastmoney-fund" if is_fund else "eastmoney",
                        is_stale=True,
                        error=error,
                        series_kind="nav" if is_fund else "intraday",
                    ),
                    now,
                )
            return self._remember(canonical, snapshot, now)
