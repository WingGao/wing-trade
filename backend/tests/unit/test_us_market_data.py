from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlparse
from urllib.request import Request

import pytest

from app.market_data.calendar import AShareTradingCalendar, OpenFundCalendar, USTradingCalendar
from app.market_data.factory import create_quote_provider
from app.market_data.provider import (
    CompositeQuoteProvider,
    EastmoneyFundQuoteProvider,
    EastmoneyQuoteProvider,
    EastmoneyUSQuoteProvider,
    FallbackUSQuoteProvider,
    TencentUSQuoteProvider,
    USMarketResolver,
)
from app.market_data.security_info import SecurityInfoProvider


def _us_dt(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    """构造带美东时区的测试时间并转换为 UTC。"""
    return datetime(year, month, day, hour, minute, tzinfo=USTradingCalendar.timezone).astimezone(UTC)


def test_us_trading_calendar_session_phases() -> None:
    """断言美股日历正确识别盘前、常规盘、盘后、休市及周末。"""
    calendar = USTradingCalendar()
    # 2026-11-23 为周一（冬令时 EST）
    assert calendar.session_phase(_us_dt(2026, 11, 23, 3, 59)) == "closed"
    assert calendar.is_session_open(_us_dt(2026, 11, 23, 3, 59)) is False

    assert calendar.session_phase(_us_dt(2026, 11, 23, 4, 0)) == "pre"
    assert calendar.is_session_open(_us_dt(2026, 11, 23, 8, 30)) is True

    assert calendar.session_phase(_us_dt(2026, 11, 23, 9, 30)) == "regular"
    assert calendar.is_session_open(_us_dt(2026, 11, 23, 12, 0)) is True
    assert calendar.session_phase(_us_dt(2026, 11, 23, 16, 0)) == "regular"

    assert calendar.session_phase(_us_dt(2026, 11, 23, 16, 1)) == "post"
    assert calendar.is_session_open(_us_dt(2026, 11, 23, 19, 30)) is True
    assert calendar.session_phase(_us_dt(2026, 11, 23, 20, 0)) == "post"

    assert calendar.session_phase(_us_dt(2026, 11, 23, 20, 1)) == "closed"
    assert calendar.is_session_open(_us_dt(2026, 11, 23, 20, 1)) is False

    # 周六休市
    assert calendar.session_phase(_us_dt(2026, 11, 21, 10, 0)) == "closed"
    assert calendar.is_session_open(_us_dt(2026, 11, 21, 10, 0)) is False
    assert AShareTradingCalendar().session_phase(
        datetime(2026, 11, 23, 10, 0, tzinfo=AShareTradingCalendar.timezone)
    ) == "regular"
    assert OpenFundCalendar().session_phase(datetime(2026, 11, 23, 10, 0, tzinfo=UTC)) == "closed"


def test_us_market_resolver_caches_and_matches_exact_code() -> None:
    """断言 USMarketResolver 精确匹配美股市场码并缓存结果，避免重复查名。"""
    calls: list[str] = []

    def transport(request: Request, _timeout: float) -> bytes:
        calls.append(request.full_url)
        return json.dumps(
            {
                "QuotationCodeTable": {
                    "Data": [
                        {"Code": "AAPL", "Name": "苹果", "MktNum": "105"},
                        {"Code": "BABA", "Name": "阿里巴巴", "MktNum": "106"},
                    ]
                }
            }
        ).encode()

    resolver = USMarketResolver(transport=transport)
    assert resolver.resolve("aapl") == "105"
    assert resolver.resolve("AAPL") == "105"
    assert resolver.get_cached("BABA") == "106"
    assert len(calls) == 1


def test_eastmoney_us_provider_regular_and_extended_hours() -> None:
    """断言美股批量源在盘前/盘中/盘后正确解析 Decimal 价格并标注交易阶段。"""
    resolver = USMarketResolver()
    resolver.remember("AAPL", "105")
    resolver.remember("BABA", "106")

    # 1. 盘前时段：优先使用 f251/f253，缺扩展价时回退常规价 f2
    pre_now = _us_dt(2026, 11, 23, 7, 30)

    def pre_transport(request: Request, _timeout: float) -> bytes:
        qs = parse_qs(urlparse(request.full_url).query)
        assert qs["secids"] == ["105.AAPL,106.BABA"]
        return json.dumps(
            {
                "data": {
                    "diff": [
                        {
                            "f12": "AAPL",
                            "f13": 105,
                            "f2": 230.00,
                            "f3": 0.50,
                            "f18": 228.85,
                            "f124": int(pre_now.timestamp()),
                            "f251": 232.50,
                            "f253": 1.59,
                        },
                        {
                            "f12": "BABA",
                            "f13": 106,
                            "f2": 95.40,
                            "f3": -0.62,
                            "f18": 96.00,
                            "f124": int(_us_dt(2026, 11, 20, 16, 0).timestamp()),
                            "f251": "-",
                            "f253": "-",
                        },
                    ]
                }
            }
        ).encode()

    provider = EastmoneyUSQuoteProvider(
        transport=pre_transport,
        now=lambda: pre_now,
        resolver=resolver,
    )
    quotes = provider.get_quotes(["US:AAPL", "US:BABA", "SH:600519"])
    assert set(quotes) == {"US:AAPL", "US:BABA"}
    assert quotes["US:AAPL"].last_price == Decimal("232.500000")
    assert quotes["US:AAPL"].change_pct == Decimal("1.590000")
    assert quotes["US:AAPL"].market_session == "pre"
    assert quotes["US:BABA"].last_price == Decimal("95.400000")
    assert quotes["US:BABA"].market_session == "regular"

    # 2. 盘后时段：若 f253 缺失则按常规收盘价 f2 计算盘后涨跌幅
    post_now = _us_dt(2026, 11, 23, 17, 15)

    def post_transport(_request: Request, _timeout: float) -> bytes:
        return json.dumps(
            {
                "data": {
                    "diff": [
                        {
                            "f12": "AAPL",
                            "f13": 105,
                            "f2": 200.00,
                            "f3": 1.00,
                            "f18": 198.00,
                            "f124": int(post_now.timestamp()),
                            "f251": 204.00,
                            "f253": "-",
                        }
                    ]
                }
            }
        ).encode()

    post_provider = EastmoneyUSQuoteProvider(
        transport=post_transport,
        now=lambda: post_now,
        resolver=resolver,
    )
    post_quotes = post_provider.get_quotes(["US:AAPL"])
    assert post_quotes["US:AAPL"].last_price == Decimal("204.000000")
    assert post_quotes["US:AAPL"].change_pct == Decimal("2.000000")
    assert post_quotes["US:AAPL"].market_session == "post"


def test_eastmoney_us_provider_probes_unknown_market_and_rejects_bad_rows() -> None:
    """断言未命中查名的美股自动探测 105/106/107 并回填缓存，同时忽略非法价格行。"""
    resolver = USMarketResolver(transport=lambda *_args: b"{}")
    regular_now = _us_dt(2026, 11, 23, 10, 30)

    def transport(request: Request, _timeout: float) -> bytes:
        qs = parse_qs(urlparse(request.full_url).query)
        assert qs["secids"] == ["105.SPY,106.SPY,107.SPY"]
        return json.dumps(
            {
                "data": {
                    "diff": [
                        {"f12": "SPY", "f13": 105, "f2": "-", "f18": "-"},
                        {"f12": "SPY", "f13": 107, "f2": 590.25, "f3": 0.42, "f18": 587.78, "f124": int(regular_now.timestamp())},
                    ]
                }
            }
        ).encode()

    provider = EastmoneyUSQuoteProvider(
        transport=transport,
        now=lambda: regular_now,
        resolver=resolver,
    )
    quotes = provider.get_quotes(["US:SPY"])
    assert quotes["US:SPY"].last_price == Decimal("590.250000")
    assert resolver.get_cached("SPY") == "107"


def test_composite_quote_provider_routes_cn_us_and_fund() -> None:
    """断言默认组合行情源支持 A 股、美股（含 VOO ETF）与场外基金三市场混合路由且互不干扰。"""
    provider = create_quote_provider()
    assert isinstance(provider, CompositeQuoteProvider)
    assert provider.supports_symbol("SH:600519") is True
    assert provider.supports_symbol("US:AAPL") is True
    assert provider.supports_symbol("US:VOO") is True
    assert provider.supports_symbol("US:BRK.B") is True
    assert provider.supports_symbol("OF:005827") is True
    assert any(isinstance(sub, EastmoneyQuoteProvider) for sub in provider.providers)
    assert any(isinstance(sub, FallbackUSQuoteProvider) for sub in provider.providers)
    assert any(isinstance(sub, EastmoneyFundQuoteProvider) for sub in provider.providers)


def test_tencent_us_provider_parses_voo_etf_and_fallback() -> None:
    """断言腾讯美股源正确解析 VOO 等美股 ETF 报价，且主源失败时自动回退东财源。"""
    now = _us_dt(2026, 11, 23, 10, 15)
    parts = [""] * 73
    parts[0] = "200"
    parts[1] = "标普500 ETF-Vanguard"
    parts[2] = "VOO.AM"
    parts[3] = "664.01"
    parts[4] = "635.54"
    parts[30] = "2026-11-23 10:12:59"
    parts[32] = "4.48"
    line = f'v_usVOO="{"~".join(parts)}";\n'.encode("gbk")

    tencent = TencentUSQuoteProvider(transport=lambda *_args: line, now=lambda: now)
    quotes = tencent.get_quotes(["US:VOO", "SH:600519"])
    assert set(quotes) == {"US:VOO"}
    assert quotes["US:VOO"].last_price == Decimal("664.010000")
    assert quotes["US:VOO"].prev_close == Decimal("635.540000")
    assert quotes["US:VOO"].change_pct == Decimal("4.480000")
    assert quotes["US:VOO"].market_session == "regular"

    # 主源异常时，FallbackUSQuoteProvider 自动回退备源
    def fail_transport(_req: Request, _timeout: float) -> bytes:
        raise ConnectionResetError("remote disconnected")

    fallback = FallbackUSQuoteProvider(
        TencentUSQuoteProvider(transport=fail_transport, now=lambda: now),
        tencent,
    )
    recovered = fallback.get_quotes(["US:VOO"])
    assert recovered["US:VOO"].last_price == Decimal("664.010000")


def test_us_intraday_falls_back_to_tencent_when_eastmoney_disconnects() -> None:
    """断言东财美股分时断连时，SecurityInfoProvider 自动回退腾讯美股分时接口。"""
    now = _us_dt(2026, 11, 23, 10, 15)

    def transport(request: Request, _timeout: float) -> bytes:
        if "eastmoney.com" in request.full_url:
            raise ConnectionResetError("Remote end closed connection")
        return json.dumps(
            {
                "code": 0,
                "data": {
                    "usVOO": {
                        "data": {
                            "date": "20261123",
                            "data": [
                                "0930 664.02 1028751",
                                "1014 664.16 5732937",
                            ],
                        },
                        "qt": {
                            "usVOO": ["200", "VOO", "VOO.AM", "664.16", "635.54"],
                        },
                    }
                },
            }
        ).encode()

    info = SecurityInfoProvider(transport=transport, now=lambda: now)
    snapshot = info.intraday("US:VOO")
    assert snapshot.provider == "tencent-us-intraday"
    assert snapshot.trading_date == "2026-11-23"
    assert len(snapshot.points) == 2
    assert snapshot.points[-1].price == Decimal("664.160000")
    assert snapshot.quote is not None
    assert snapshot.quote.prev_close == Decimal("635.540000")
    assert snapshot.is_stale is False


def test_us_intraday_uses_iscr_1_and_handles_midnight_cross() -> None:
    """断言美股分时开启盘前盘后 iscr=1，且北京时间次日凌晨仍归属同一美东交易日。"""
    # 美东 2026-11-23 16:30（盘后）= 北京时间 2026-11-24 05:30（已跨北京午夜）
    now = _us_dt(2026, 11, 23, 16, 30)
    resolver = USMarketResolver()
    resolver.remember("AAPL", "105")

    def transport(request: Request, _timeout: float) -> bytes:
        qs = parse_qs(urlparse(request.full_url).query)
        assert qs["secid"] == ["105.AAPL"]
        assert qs["iscr"] == ["1"]
        return json.dumps(
            {
                "data": {
                    "code": "AAPL",
                    "market": 105,
                    "preClose": 228.00,
                    "trends": [
                        # 美东 2026-11-23 08:00（盘前）= 北京 2026-11-23 21:00
                        "2026-11-23 21:00,229.00,229.50,229.50,229.00,100,22950,229.25",
                        # 美东 2026-11-23 16:29（盘后）= 北京 2026-11-24 05:29
                        "2026-11-24 05:29,231.00,231.80,231.80,231.00,200,46360,231.40",
                    ],
                }
            }
        ).encode()

    info = SecurityInfoProvider(transport=transport, now=lambda: now, us_resolver=resolver)
    snapshot = info.intraday("US:AAPL")
    assert snapshot.trading_date == "2026-11-23"
    assert len(snapshot.points) == 2
    assert snapshot.points[-1].price == Decimal("231.800000")
    assert snapshot.quote is not None
    assert snapshot.quote.market_session == "post"
    assert snapshot.is_stale is False

    # 若最后一个点超过 2 分钟未更新且仍在盘后，则应标记过期
    late_info = SecurityInfoProvider(
        transport=transport,
        now=lambda: now + timedelta(minutes=5),
        us_resolver=resolver,
    )
    late_snapshot = late_info.intraday("US:AAPL")
    assert late_snapshot.is_stale is True
