from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import pytest

from app.config import Settings
from app.domain.markets import currency_for_market, is_open_fund_market
from app.market_data import (
    CompositeQuoteProvider,
    EastmoneyFundQuoteProvider,
    EastmoneyQuoteProvider,
    OpenFundCalendar,
    create_quote_provider,
)
from app.market_data.security_info import IntradayService, SecurityInfoProvider

NOW = datetime(2026, 11, 20, 12, 30, tzinfo=UTC)


def fund_lsjz_payload(rows: list[dict[str, object]]) -> bytes:
    """构造东方财富场外基金历史已公布单位净值响应。"""
    return json.dumps({"Data": {"LSJZList": rows}, "ErrCode": 0}, ensure_ascii=False).encode()


def test_open_fund_market_currency_and_calendar() -> None:
    """场外基金市场归属 CNY，且按日净值日历不会在盘中误判过期。"""
    assert currency_for_market("OF") == "CNY"
    assert currency_for_market("of") == "CNY"
    assert is_open_fund_market("OF") is True
    assert is_open_fund_market("SH") is False

    calendar = OpenFundCalendar()
    trading_time = datetime(2026, 11, 20, 2, 0, tzinfo=UTC)  # 北京时间 10:00
    assert calendar.is_session_open(trading_time) is False
    assert calendar.is_stale(trading_time - timedelta(hours=2), now=trading_time) is False
    assert calendar.is_stale(trading_time - timedelta(hours=7), now=trading_time) is True


def test_eastmoney_fund_provider_parses_published_nav_and_change() -> None:
    """正确解析已公布单位净值、前一日净值、涨跌幅与上海收盘时间。"""
    calls: list[tuple[str, str | None]] = []

    def transport(request, _timeout):
        """按基金代码返回包含官方涨跌幅和缺省涨跌幅的两组样本。"""
        calls.append((request.full_url, request.get_header("Referer")))
        code = parse_qs(urlparse(request.full_url).query)["fundCode"][0]
        if code == "005827":
            return fund_lsjz_payload(
                [
                    {"FSRQ": "2026-11-20", "DWJZ": "1.8234", "JZZZL": "1.30"},
                    {"FSRQ": "2026-11-19", "DWJZ": "1.8000", "JZZZL": "-0.50"},
                ]
            )
        return fund_lsjz_payload(
            [
                {"FSRQ": "2026-11-20", "DWJZ": "2.2000", "JZZZL": ""},
                {"FSRQ": "2026-11-19", "DWJZ": "2.0000", "JZZZL": "0.00"},
            ]
        )

    provider = EastmoneyFundQuoteProvider(transport=transport, now=lambda: NOW)
    assert provider.supports_symbol("OF:005827") is True
    assert provider.supports_symbol("SH:600519") is False
    assert provider.supports_symbol("OF:12345") is False

    quotes = provider.get_quotes(["OF:005827", "OF:110011", "SH:600519"])
    assert set(quotes) == {"OF:005827", "OF:110011"}
    assert calls[0][1] == "https://fundf10.eastmoney.com/"

    bluechip = quotes["OF:005827"]
    assert bluechip.last_price == Decimal("1.823400")
    assert bluechip.prev_close == Decimal("1.800000")
    assert bluechip.change_pct == Decimal("1.300000")
    assert bluechip.source_time == datetime(2026, 11, 20, 7, 0, tzinfo=UTC)
    assert bluechip.fetched_at == NOW
    assert bluechip.provider == "eastmoney-fund"

    # 涨跌幅为空时由最新净值与前一日净值自动精确计算
    computed = quotes["OF:110011"]
    assert computed.change_pct == Decimal("10.000000")


def test_eastmoney_fund_provider_handles_invalid_rows_partial_and_total_failure() -> None:
    """跳过非法净值行；单只基金失败不阻塞其余基金；全部失败抛出异常。"""

    def partial_transport(request, _timeout):
        """一只成功（带无效首行），一只超时。"""
        code = parse_qs(urlparse(request.full_url).query)["fundCode"][0]
        if code == "005827":
            return fund_lsjz_payload(
                [
                    {"FSRQ": "2026-11-20", "DWJZ": "", "JZZZL": ""},
                    {"FSRQ": "2026-11-19", "DWJZ": "0", "JZZZL": "0"},
                    {"FSRQ": "2026-11-18", "DWJZ": "1.2345", "JZZZL": "0.50"},
                ]
            )
        raise TimeoutError("fund timeout")

    provider = EastmoneyFundQuoteProvider(transport=partial_transport, now=lambda: NOW)
    quotes = provider.get_quotes(["OF:005827", "OF:110011"])
    assert list(quotes) == ["OF:005827"]
    assert quotes["OF:005827"].last_price == Decimal("1.234500")

    failing = EastmoneyFundQuoteProvider(
        transport=lambda *_: (_ for _ in ()).throw(TimeoutError("all failed")),
        now=lambda: NOW,
    )
    with pytest.raises(TimeoutError, match="all failed"):
        failing.get_quotes(["OF:005827"])

    oversized = EastmoneyFundQuoteProvider(transport=lambda *_: b"x" * 16)
    oversized._max_response_bytes = 8
    with pytest.raises(ValueError, match="size limit"):
        oversized.get_quotes(["OF:005827"])


def test_composite_quote_provider_routes_stocks_and_funds_independently() -> None:
    """组合行情源按市场路由，且单源异常不丢弃另一源的有效报价。"""

    def stock_transport(_request, _timeout):
        """模拟 A 股批量行情响应。"""
        return json.dumps(
            {
                "data": {
                    "diff": [
                        {
                            "f12": "600519",
                            "f13": 1,
                            "f2": "1500.00",
                            "f3": "1.25",
                            "f18": "1481.48",
                            "f124": 1795071600,
                        }
                    ]
                }
            }
        ).encode()

    def fund_transport(_request, _timeout):
        """模拟场外基金净值响应。"""
        return fund_lsjz_payload(
            [
                {"FSRQ": "2026-11-20", "DWJZ": "1.5678", "JZZZL": "0.80"},
                {"FSRQ": "2026-11-19", "DWJZ": "1.5554", "JZZZL": "0.10"},
            ]
        )

    composite = CompositeQuoteProvider(
        [
            EastmoneyQuoteProvider(transport=stock_transport, now=lambda: NOW),
            EastmoneyFundQuoteProvider(transport=fund_transport, now=lambda: NOW),
        ]
    )
    quotes = composite.get_quotes(["SH:600519", "OF:005827"])
    assert quotes["SH:600519"].last_price == Decimal("1500.000000")
    assert quotes["OF:005827"].last_price == Decimal("1.567800")

    # 股票源超时时，基金源结果仍正常返回
    degraded = CompositeQuoteProvider(
        [
            EastmoneyQuoteProvider(
                transport=lambda *_: (_ for _ in ()).throw(TimeoutError("stock down")),
                now=lambda: NOW,
            ),
            EastmoneyFundQuoteProvider(transport=fund_transport, now=lambda: NOW),
        ]
    )
    partial = degraded.get_quotes(["SH:600519", "OF:005827"])
    assert list(partial) == ["OF:005827"]


def test_factory_eastmoney_supports_ashare_and_open_fund_offline_health(monkeypatch) -> None:
    """默认 eastmoney 工厂同时支持 A 股、美股与场外基金，且健康检查不联网。"""
    monkeypatch.setenv("QUOTE_PROVIDER", "eastmoney")
    provider = create_quote_provider(Settings.from_env())
    assert isinstance(provider, CompositeQuoteProvider)
    assert provider.supports_symbol("SH:600519") is True
    assert provider.supports_symbol("OF:005827") is True
    assert provider.supports_symbol("US:AAPL") is True
    assert provider.supports_symbol("HK:00700") is False
    assert provider.health().healthy is True


def test_security_info_search_distinguishes_etf_and_open_fund() -> None:
    """查名保留场内 ETF 归属沪深市场，并将场外基金识别为 OF / FUND。"""

    def transport(_request, _timeout):
        """返回混合场内 ETF、场外基金和股票的查名样本。"""
        return json.dumps(
            {
                "QuotationCodeTable": {
                    "Data": [
                        {
                            "Code": "510300",
                            "Name": "沪深300ETF",
                            "MktNum": "1",
                            "Classify": "Fund",
                        },
                        {
                            "Code": "005827",
                            "Name": "易方达蓝筹精选混合",
                            "MktNum": "",
                            "Classify": "Fund",
                            "SecurityTypeName": "开放式基金",
                            "QuoteID": "7.005827",
                        },
                        {
                            "Code": "110011",
                            "Name": "易方达中小盘混合",
                            "MktNum": "7",
                            "Classify": "OpenFund",
                        },
                    ]
                }
            },
            ensure_ascii=False,
        ).encode()

    provider = SecurityInfoProvider(transport=transport)
    all_rows = provider.search("易方达")
    assert [(r.market, r.symbol, r.security_type) for r in all_rows] == [
        ("SH", "510300", "STOCK"),
        ("OF", "005827", "FUND"),
        ("OF", "110011", "FUND"),
    ]
    only_of = provider.search("OF:005827")
    assert [(r.market, r.symbol, r.security_type) for r in only_of] == [
        ("OF", "005827", "FUND"),
        ("OF", "110011", "FUND"),
    ]


def test_security_info_fund_nav_history_and_cache() -> None:
    """场外基金返回按日期升序的近期净值曲线，并在 5 分钟内命中缓存、失败保留旧缓存。"""
    clock = [NOW]
    failing = [False]
    calls = 0

    def transport(request, _timeout):
        """返回倒序历史净值并允许模拟网络故障。"""
        nonlocal calls
        calls += 1
        assert "f10/lsjz" in request.full_url
        if failing[0]:
            raise TimeoutError("fund nav offline")
        return fund_lsjz_payload(
            [
                {"FSRQ": "2026-11-20", "DWJZ": "1.8234", "JZZZL": "1.30"},
                {"FSRQ": "2026-11-19", "DWJZ": "1.8000", "JZZZL": "-0.50"},
                {"FSRQ": "2026-11-18", "DWJZ": "1.8090", "JZZZL": "0.20"},
            ]
        )

    async def scenario() -> None:
        """验证升序曲线、5 分钟防抖缓存与断网过期回退。"""
        service = IntradayService(SecurityInfoProvider(transport=transport, now=lambda: clock[0]))
        first = await service.read("OF:005827")
        assert first.series_kind == "nav"
        assert first.trading_date == "2026-11-20"
        assert [p.price for p in first.points] == [
            Decimal("1.809000"),
            Decimal("1.800000"),
            Decimal("1.823400"),
        ]
        assert first.quote is not None
        assert first.quote.last_price == Decimal("1.823400")
        assert first.quote.prev_close == Decimal("1.800000")
        assert not first.is_stale

        # 2 分钟内命中缓存不重复请求上游
        clock[0] += timedelta(minutes=2)
        cached = await service.read("OF:005827")
        assert cached == first
        assert calls == 1

        # 超过 5 分钟且断网时保留历史净值曲线并标为过期
        clock[0] += timedelta(minutes=4)
        failing[0] = True
        fallback = await service.read("OF:005827")
        assert fallback.series_kind == "nav"
        assert fallback.points == first.points
        assert fallback.is_stale is True
        assert fallback.quote is not None and fallback.quote.is_stale is True
        assert calls == 2

    asyncio.run(scenario())
