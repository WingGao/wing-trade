"""证券日 K 线近 20 个已收盘交易日平均振幅拉取与持久化缓存服务。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from urllib.parse import urlencode
from urllib.request import Request

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.precision import ensure_decimal, round_price
from app.models.ledger import Security, SecurityDailyMetric

from .calendar import AShareTradingCalendar, USTradingCalendar
from .provider import (
    EastmoneyQuoteProvider,
    USMarketResolver,
    canonical_symbol,
    shared_us_market_resolver,
)

logger = logging.getLogger("ashare_ledger.market_data.amplitude")

_ASHARE_MARKETS = frozenset({"SH", "SZ", "BJ"})
_US_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,11}$")
_US_UPSTREAM_MARKETS = ("105", "106", "107")
_DEFAULT_WINDOW_DAYS = 20


def _now_utc() -> datetime:
    """返回带 UTC 时区的当前时间。"""
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    """将无时区或其它时区的时间统一规整为 UTC。"""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class DailyAmplitudeSnapshot:
    """单只证券近 N 个已收盘交易日平均振幅的精确快照。"""

    symbol: str
    avg_amplitude_20d: Decimal
    sample_days: int
    as_of_date: str
    fetched_at: datetime
    provider: str


class DailyAmplitudeProvider:
    """从东方财富（A 股/美股主源）及腾讯（美股备源）拉取日 K 线并计算近 20 日已收盘平均振幅。"""

    _eastmoney_kline_url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    _tencent_us_kline_url = "https://web.ifzq.gtimg.cn/appstock/app/usfqkline/get"
    _max_response_bytes = 2 * 1024 * 1024

    def __init__(
        self,
        *,
        transport: Callable[[Request, float], bytes] | None = None,
        timeout_seconds: Decimal | str | int | float = Decimal("5"),
        now: Callable[[], datetime] | None = None,
        a_calendar: AShareTradingCalendar | None = None,
        us_calendar: USTradingCalendar | None = None,
        us_resolver: USMarketResolver | None = None,
    ) -> None:
        """初始化日 K 线拉取器，支持测试注入传输层、时钟与美股交易所解析器。"""
        timeout = (
            Decimal(str(timeout_seconds))
            if isinstance(timeout_seconds, float)
            else ensure_decimal(timeout_seconds, field="timeout_seconds")
        )
        if not timeout.is_finite() or timeout <= 0:
            raise ValueError("timeout_seconds must be a finite positive number")
        self.timeout_seconds = float(timeout)
        self._transport = transport or EastmoneyQuoteProvider()._default_transport
        self._now = now or _now_utc
        self.a_calendar = a_calendar or AShareTradingCalendar()
        self.us_calendar = us_calendar or USTradingCalendar()
        self._us_resolver = (
            us_resolver
            if us_resolver is not None
            else (
                USMarketResolver(transport=transport, timeout_seconds=timeout)
                if transport is not None
                else shared_us_market_resolver()
            )
        )

    @staticmethod
    def supports_symbol(symbol: str) -> bool:
        """判断证券是否支持日 K 振幅统计（仅 A 股 SH/SZ/BJ 与美股 US，排除场外基金 OF）。"""
        canonical = canonical_symbol(symbol)
        if ":" not in canonical:
            return False
        market, code = canonical.split(":", 1)
        if market in _ASHARE_MARKETS:
            return len(code) == 6 and code.isdigit()
        if market == "US":
            return bool(_US_SYMBOL_RE.match(code))
        return False

    def max_closed_date(self, market: str, now: datetime) -> date:
        """返回指定市场在当前时刻允许纳入统计的最大已收盘自然日（严格排除当日盘中未收盘）。"""
        current = _as_utc(now)
        normalized_market = market.upper()
        if normalized_market == "US":
            local = current.astimezone(self.us_calendar.timezone)
            if self.us_calendar.is_trading_day(local) and local.time().replace(tzinfo=None) < time(16, 0):
                return local.date() - timedelta(days=1)
            return local.date()
        local = current.astimezone(self.a_calendar.timezone)
        if self.a_calendar.is_trading_day(local) and local.time().replace(tzinfo=None) < time(15, 0):
            return local.date() - timedelta(days=1)
        return local.date()

    def expected_closed_date(self, market: str, now: datetime) -> date:
        """推算指定市场理论上的最近一个已收盘交易日，用于判断本地缓存是否新鲜。"""
        normalized_market = market.upper()
        calendar = self.us_calendar if normalized_market == "US" else self.a_calendar
        candidate = self.max_closed_date(normalized_market, now)
        for _ in range(14):
            if calendar.is_trading_day(candidate):
                return candidate
            candidate -= timedelta(days=1)
        return candidate

    def _request_json(
        self,
        url: str,
        *,
        referer: str = "https://quote.eastmoney.com/",
    ) -> Mapping[str, object]:
        """发起有界 HTTP GET 请求并按精确 Decimal 解析 JSON 对象。"""
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "wing-trade/0.1 (+backend market data)",
                "Referer": referer,
            },
        )
        body = self._transport(request, self.timeout_seconds)
        if len(body) > self._max_response_bytes:
            raise ValueError("daily kline response exceeds size limit")
        text = body.decode("utf-8-sig") if isinstance(body, bytes) else str(body)
        payload = json.loads(
            text,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=EastmoneyQuoteProvider._reject_json_constant,
        )
        if not isinstance(payload, Mapping):
            raise ValueError("daily kline response must be a JSON object")
        return payload

    @staticmethod
    def _parse_date(raw: object) -> date | None:
        """解析 YYYY-MM-DD 日期字符串，格式无效时返回 None。"""
        text = str(raw or "").strip()[:10]
        if len(text) != 10:
            return None
        try:
            return datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError:
            return None

    def _fetch_eastmoney_kline_bars(
        self,
        secid: str,
        *,
        limit: int,
    ) -> list[tuple[date, Decimal]]:
        """从东方财富日 K 线接口拉取并解析每日已验证振幅列表（按日期升序）。"""
        query = urlencode(
            {
                "secid": secid,
                "klt": "101",
                "fqt": "0",
                "lmt": str(limit),
                "end": "20500101",
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
            }
        )
        payload = self._request_json(f"{self._eastmoney_kline_url}?{query}")
        data = payload.get("data")
        if not isinstance(data, Mapping):
            return []
        klines = data.get("klines")
        if not isinstance(klines, list):
            return []

        raw_rows: dict[date, tuple[Decimal, Decimal, Decimal, Decimal | None]] = {}
        for item in klines:
            if not isinstance(item, str):
                continue
            parts = item.split(",")
            if len(parts) < 5:
                continue
            bar_date = self._parse_date(parts[0])
            if bar_date is None:
                continue
            try:
                close_price = EastmoneyQuoteProvider._optional_decimal(parts[2], field_name="close")
                high_price = EastmoneyQuoteProvider._optional_decimal(parts[3], field_name="high")
                low_price = EastmoneyQuoteProvider._optional_decimal(parts[4], field_name="low")
                amp_field = (
                    EastmoneyQuoteProvider._optional_decimal(parts[7], field_name="amplitude")
                    if len(parts) >= 8
                    else None
                )
            except (TypeError, ValueError):
                continue
            if (
                close_price is None
                or high_price is None
                or low_price is None
                or close_price <= 0
                or high_price <= 0
                or low_price <= 0
                or high_price < low_price
            ):
                continue
            raw_rows[bar_date] = (close_price, high_price, low_price, amp_field)

        result: list[tuple[date, Decimal]] = []
        prev_close: Decimal | None = None
        for bar_date in sorted(raw_rows):
            close_price, high_price, low_price, amp_field = raw_rows[bar_date]
            amplitude: Decimal | None = None
            if amp_field is not None and amp_field >= 0:
                amplitude = amp_field
            elif prev_close is not None and prev_close > 0:
                amplitude = (high_price - low_price) / prev_close * Decimal("100")
            prev_close = close_price
            if amplitude is not None and amplitude >= 0:
                result.append((bar_date, amplitude))
        return result

    def _fetch_tencent_us_kline_bars(
        self,
        code: str,
        *,
        limit: int,
    ) -> list[tuple[date, Decimal]]:
        """从腾讯美股日 K 线接口拉取并按 (高-低)/昨收×100% 计算每日振幅（按日期升序）。"""
        query = urlencode({"param": f"us{code},day,,,{limit},qfq"})
        payload = self._request_json(
            f"{self._tencent_us_kline_url}?{query}",
            referer="https://gu.qq.com/",
        )
        data = payload.get("data")
        if not isinstance(data, Mapping):
            return []
        symbol_node = data.get(f"us{code}")
        if not isinstance(symbol_node, Mapping):
            return []
        rows = symbol_node.get("qfqday") or symbol_node.get("day")
        if not isinstance(rows, list):
            return []

        raw_rows: dict[date, tuple[Decimal, Decimal, Decimal]] = {}
        for item in rows:
            if not isinstance(item, list) or len(item) < 5:
                continue
            bar_date = self._parse_date(item[0])
            if bar_date is None:
                continue
            try:
                close_price = EastmoneyQuoteProvider._optional_decimal(item[2], field_name="close")
                high_price = EastmoneyQuoteProvider._optional_decimal(item[3], field_name="high")
                low_price = EastmoneyQuoteProvider._optional_decimal(item[4], field_name="low")
            except (TypeError, ValueError):
                continue
            if (
                close_price is None
                or high_price is None
                or low_price is None
                or close_price <= 0
                or high_price <= 0
                or low_price <= 0
                or high_price < low_price
            ):
                continue
            raw_rows[bar_date] = (close_price, high_price, low_price)

        result: list[tuple[date, Decimal]] = []
        prev_close: Decimal | None = None
        for bar_date in sorted(raw_rows):
            close_price, high_price, low_price = raw_rows[bar_date]
            if prev_close is not None and prev_close > 0:
                amplitude = (high_price - low_price) / prev_close * Decimal("100")
                if amplitude >= 0:
                    result.append((bar_date, amplitude))
            prev_close = close_price
        return result

    def _fetch_bars_for_symbol(
        self,
        canonical: str,
        *,
        limit: int,
    ) -> tuple[list[tuple[date, Decimal]], str]:
        """按市场路由拉取单只证券的日 K 振幅序列，美股支持东财失败时回退腾讯源。"""
        market, code = canonical.split(":", 1)
        if market in _ASHARE_MARKETS:
            upstream_market = 1 if market == "SH" else 0
            bars = self._fetch_eastmoney_kline_bars(f"{upstream_market}.{code}", limit=limit)
            return bars, "eastmoney-kline"

        # 美股：优先通过解析器定位交易所编号拉取东财日 K，失败或无数据时回退腾讯美股日 K。
        resolved_market = self._us_resolver.resolve(
            code,
            transport=self._transport,
            timeout_seconds=self.timeout_seconds,
        )
        candidate_markets = (resolved_market,) if resolved_market else _US_UPSTREAM_MARKETS
        for candidate in candidate_markets:
            try:
                bars = self._fetch_eastmoney_kline_bars(f"{candidate}.{code}", limit=limit)
            except Exception:
                bars = []
            if bars:
                self._us_resolver.remember(code, candidate)
                return bars, "eastmoney-us-kline"

        bars = self._fetch_tencent_us_kline_bars(code, limit=limit)
        return bars, "tencent-us-kline"

    def fetch_avg_amplitude(
        self,
        symbol: str,
        *,
        window: int = _DEFAULT_WINDOW_DAYS,
        now: datetime | None = None,
    ) -> DailyAmplitudeSnapshot | None:
        """拉取单只证券最近 window 个已收盘交易日日 K 并计算精确平均振幅。"""
        if window <= 0:
            raise ValueError("window must be positive")
        canonical = canonical_symbol(symbol)
        if not self.supports_symbol(canonical):
            return None
        market, _ = canonical.split(":", 1)
        current = _as_utc(now or self._now())
        max_date = self.max_closed_date(market, current)
        # 多拉取若干根日 K，保证在过滤当日未收盘数据或首根无昨收后仍凑足 window 个交易日。
        bars, provider_name = self._fetch_bars_for_symbol(canonical, limit=window + 5)
        closed_bars = [(bar_date, amp) for bar_date, amp in bars if bar_date <= max_date]
        if not closed_bars:
            return None
        selected = closed_bars[-window:]
        total = sum((amp for _, amp in selected), Decimal("0"))
        avg = round_price(total / Decimal(len(selected)))
        return DailyAmplitudeSnapshot(
            symbol=canonical,
            avg_amplitude_20d=avg,
            sample_days=len(selected),
            as_of_date=selected[-1][0].isoformat(),
            fetched_at=current,
            provider=provider_name,
        )


class DailyAmplitudeService:
    """管理证券 20 日平均振幅的数据库缓存读取、按交易日按需刷新与节假日/失败冷却。"""

    def __init__(
        self,
        provider: DailyAmplitudeProvider | None = None,
        *,
        network_enabled: bool = True,
        attempt_cooldown_seconds: int = 1800,
        max_concurrency: int = 4,
        batch_timeout_seconds: float = 4.0,
        window_days: int = _DEFAULT_WINDOW_DAYS,
    ) -> None:
        """初始化振幅缓存服务、并发上限、单次批量超时与失败冷却时间。"""
        self.provider = provider or DailyAmplitudeProvider()
        self.network_enabled = network_enabled
        self.attempt_cooldown_seconds = max(0, attempt_cooldown_seconds)
        self.max_concurrency = max(1, max_concurrency)
        self.batch_timeout_seconds = max(0.5, batch_timeout_seconds)
        self.window_days = max(1, window_days)
        self._last_attempt: dict[str, datetime] = {}

    @staticmethod
    def _security_symbol(security: Security) -> str:
        """返回证券对应的规范行情代码。"""
        return canonical_symbol(f"{security.market}:{security.symbol}")

    def read_cached(
        self,
        session: Session,
        securities: Sequence[Security],
    ) -> dict[str, DailyAmplitudeSnapshot]:
        """批量读取数据库中已缓存的证券 20 日平均振幅，排除场外基金与无效记录。"""
        eligible_by_id = {
            sec.id: sec
            for sec in securities
            if self.provider.supports_symbol(self._security_symbol(sec))
        }
        if not eligible_by_id:
            return {}
        rows = session.scalars(
            select(SecurityDailyMetric).where(
                SecurityDailyMetric.security_id.in_(list(eligible_by_id))
            )
        ).all()
        result: dict[str, DailyAmplitudeSnapshot] = {}
        for row in rows:
            if (
                row.avg_amplitude_20d is None
                or row.amplitude_sample_days is None
                or row.amplitude_sample_days <= 0
                or not row.amplitude_as_of_date
            ):
                continue
            security = eligible_by_id.get(row.security_id)
            if security is None:
                continue
            result[row.security_id] = DailyAmplitudeSnapshot(
                symbol=self._security_symbol(security),
                avg_amplitude_20d=round_price(row.avg_amplitude_20d),
                sample_days=row.amplitude_sample_days,
                as_of_date=row.amplitude_as_of_date,
                fetched_at=_as_utc(row.fetched_at) if row.fetched_at is not None else _now_utc(),
                provider=row.provider or "cache",
            )
        return result

    def _is_fresh(
        self,
        security: Security,
        snapshot: DailyAmplitudeSnapshot | None,
        *,
        now: datetime,
    ) -> bool:
        """判断缓存记录是否已覆盖当前市场理论上最新的已收盘交易日。"""
        if snapshot is None:
            return False
        expected = self.provider.expected_closed_date(security.market, now).isoformat()
        return snapshot.as_of_date >= expected

    def _in_cooldown(self, security_id: str, *, now: datetime) -> bool:
        """判断该证券是否在近期尝试冷却期内，避免节假日或上游故障时高频重复请求。"""
        last = self._last_attempt.get(security_id)
        if last is None:
            return False
        return (now - last) < timedelta(seconds=self.attempt_cooldown_seconds)

    def _upsert_snapshots(
        self,
        session: Session,
        snapshots_by_security_id: Mapping[str, DailyAmplitudeSnapshot],
    ) -> None:
        """将本次成功拉取的振幅指标写入 security_daily_metrics 表。"""
        if not snapshots_by_security_id:
            return
        for security_id, snap in snapshots_by_security_id.items():
            row = session.get(SecurityDailyMetric, security_id)
            if row is None:
                row = SecurityDailyMetric(security_id=security_id)
                session.add(row)
            row.avg_amplitude_20d = round_price(snap.avg_amplitude_20d)
            row.amplitude_sample_days = snap.sample_days
            row.amplitude_as_of_date = snap.as_of_date
            row.fetched_at = _as_utc(snap.fetched_at)
            row.provider = snap.provider
        session.flush()

    async def get_or_refresh(
        self,
        session: Session,
        securities: Sequence[Security],
        *,
        allow_refresh: bool = True,
        now: datetime | None = None,
    ) -> dict[str, DailyAmplitudeSnapshot]:
        """优先返回本地缓存，必要时并发受限地按需刷新缺失或过期的 20 日平均振幅。"""
        current = _as_utc(now or _now_utc())
        cached = self.read_cached(session, securities)
        if not allow_refresh or not self.network_enabled:
            return cached

        to_refresh: list[Security] = []
        seen_ids: set[str] = set()
        for security in securities:
            if security.id in seen_ids:
                continue
            seen_ids.add(security.id)
            symbol = self._security_symbol(security)
            if not self.provider.supports_symbol(symbol):
                continue
            if self._is_fresh(security, cached.get(security.id), now=current):
                continue
            if self._in_cooldown(security.id, now=current):
                continue
            to_refresh.append(security)

        if not to_refresh:
            return cached

        for security in to_refresh:
            self._last_attempt[security.id] = current

        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def _fetch_one(sec: Security) -> tuple[str, DailyAmplitudeSnapshot | None]:
            """在信号量控制下于工作线程拉取单只证券的 20 日平均振幅。"""
            symbol = self._security_symbol(sec)
            async with semaphore:
                try:
                    snap = await asyncio.to_thread(
                        self.provider.fetch_avg_amplitude,
                        symbol,
                        window=self.window_days,
                        now=current,
                    )
                    return sec.id, snap
                except Exception as exc:
                    logger.warning(
                        "daily amplitude fetch failed",
                        extra={"symbol": symbol, "error_type": type(exc).__name__},
                    )
                    return sec.id, None

        tasks = [asyncio.create_task(_fetch_one(sec)) for sec in to_refresh]
        done, pending = await asyncio.wait(tasks, timeout=self.batch_timeout_seconds)
        for task in pending:
            task.cancel()

        fresh_updates: dict[str, DailyAmplitudeSnapshot] = {}
        for task in done:
            if task.cancelled():
                continue
            try:
                sec_id, snap = task.result()
            except Exception:
                continue
            if snap is not None:
                fresh_updates[sec_id] = snap
                cached[sec_id] = snap

        if fresh_updates:
            try:
                self._upsert_snapshots(session, fresh_updates)
                session.commit()
            except Exception:
                session.rollback()
                logger.warning("failed to persist daily amplitude metrics", exc_info=True)

        return cached
