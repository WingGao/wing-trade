from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import pytest

from app.market_data.security_info import IntradayService, SecurityInfoProvider

NOW = datetime(2026, 11, 17, 2, 30, tzinfo=UTC)


def trends_payload(*, code="600519", market=1, rows=None) -> bytes:
    """构造真实字段形状的分钟序列响应。"""
    return json.dumps(
        {
            "data": {
                "code": code,
                "market": market,
                "preClose": "10.000000",
                "trends": rows
                if rows is not None
                else [
                    "2026-11-17 10:29,10,10.123,10,10,100,1000,10",
                    "2026-11-17 10:30,10,10.125,10,10,100,1000,10",
                ],
            }
        }
    ).encode()


def test_lookup_calls_external_and_preserves_market_names() -> None:
    """查名保留真实名称、前导零、市场，并排除不支持和无名称记录。"""
    calls = []

    def transport(request, timeout):
        """记录外部请求并返回多市场候选。"""
        calls.append((request.full_url, timeout))
        return json.dumps(
            {
                "QuotationCodeTable": {
                    "Data": [
                        {"Code": "600519", "Name": "贵州茅台", "MktNum": "1"},
                        {"Code": "000001", "Name": "平安银行", "MktNum": "0"},
                        {"Code": "430047", "Name": "诺思兰德", "MktNum": "0"},
                        {"Code": "AAPL", "Name": "苹果", "MktNum": "105"},
                        {"Code": "00700", "Name": "腾讯控股", "MktNum": "116"},
                        {"Code": "600519", "Name": "贵州茅台", "MktNum": "1"},
                        {"Code": "600000", "MktNum": "1"},
                    ]
                }
            },
            ensure_ascii=False,
        ).encode()

    result = SecurityInfoProvider(transport=transport).search("茅台")
    assert [(row.market, row.symbol, row.name) for row in result] == [
        ("SH", "600519", "贵州茅台"),
        ("SZ", "000001", "平安银行"),
        ("BJ", "430047", "诺思兰德"),
        ("US", "AAPL", "苹果"),
    ]
    assert parse_qs(urlparse(calls[0][0]).query)["input"] == ["茅台"]
    assert calls[0][1] == 5
    assert [
        (row.market, row.symbol)
        for row in SecurityInfoProvider(transport=transport).search("SH:600519")
    ] == [("SH", "600519")]


def test_intraday_exact_prices_timezone_and_full_current_day() -> None:
    """首读返回完整当日序列和同次当前价，价格没有浮点误差。"""
    calls = []

    def transport(request, _timeout):
        """返回上游按开高收低字段排列的真实样本。"""
        calls.append(request.full_url)
        return trends_payload()

    result = SecurityInfoProvider(transport=transport, now=lambda: NOW).intraday("sh:600519")
    assert result.trading_date == "2026-11-17"
    assert len(result.points) == 2
    assert result.points[-1].price == Decimal("10.125000")
    assert result.points[-1].time == NOW
    assert result.quote.last_price == Decimal("10.125000")
    assert result.quote.change_pct == Decimal("1.250000")
    assert not result.is_stale
    assert parse_qs(urlparse(calls[0]).query)["ndays"] == ["1"]


def test_us_intraday_resolves_market_and_keeps_cross_midnight_session() -> None:
    """美股以纽约交易日归属，东方财富北京时间跨午夜时仍是一条完整曲线。"""
    calls = []

    def transport(request, _timeout):
        """复现公开接口美股105市场及北京时间分钟字段。"""
        calls.append(request.full_url)
        if "suggest/get" in request.full_url:
            return json.dumps(
                {
                    "QuotationCodeTable": {
                        "Data": [
                            {
                                "Code": "AAPL",
                                "Name": "苹果",
                                "MktNum": "105",
                                "Classify": "UsStock",
                            },
                        ]
                    }
                }
            ).encode()
        return trends_payload(
            code="AAPL",
            market=105,
            rows=[
                "2026-11-16 22:30,357.100,356.920,357.100,356.860,100,1000,356.3523",
                "2026-11-17 05:00,352.230,352.780,352.960,352.180,100,1000,353.2461",
            ],
        )

    provider = SecurityInfoProvider(
        transport=transport, now=lambda: datetime(2026, 11, 16, 21, 0, tzinfo=UTC)
    )
    result = provider.intraday("US:AAPL")
    assert result.trading_date == "2026-11-16" and len(result.points) == 2
    assert result.quote.source_time == datetime(2026, 11, 16, 21, 0, tzinfo=UTC)
    assert result.quote.last_price == Decimal("352.780000") and not result.is_stale
    assert parse_qs(urlparse(calls[-1]).query)["secid"] == ["105.AAPL"]
    provider.intraday("US:AAPL")
    assert sum("suggest/get" in url for url in calls) == 1


def test_intraday_filters_bad_previous_and_future_points_and_orders() -> None:
    """旧日、未来、非法和零价点不能冒充当前报价，重复点稳定归一。"""
    rows = [
        "2026-11-17 10:30,0,10.125,0,0,0,0,0",
        "2026-11-16 10:30,0,999,0,0,0,0,0",
        "2026-11-17 10:31,0,999,0,0,0,0,0",
        "2026-11-17 10:28,0,0,0,0,0,0,0",
        "2026-11-17 10:27,0,NaN,0,0,0,0,0",
        "invalid",
        "2026-11-17 10:29,0,10.123,0,0,0,0,0",
        "2026-11-17 10:29,0,10.124,0,0,0,0,0",
    ]
    provider = SecurityInfoProvider(transport=lambda *_: trends_payload(rows=rows), now=lambda: NOW)
    result = provider.intraday("SH:600519")
    assert [point.price for point in result.points] == [Decimal("10.124"), Decimal("10.125")]


@pytest.mark.parametrize("code,market", [("600000", 1), ("600519", 0)])
def test_intraday_rejects_other_security_or_market(code, market) -> None:
    """外部串码不能更新当前证券价格。"""
    provider = SecurityInfoProvider(
        transport=lambda *_: trends_payload(code=code, market=market), now=lambda: NOW
    )
    with pytest.raises(ValueError, match="不一致"):
        provider.intraday("SH:600519")


def test_intraday_stale_source_is_not_fresh_because_http_succeeded() -> None:
    """交易时段长时间未更新的分钟价必须标记过期。"""
    provider = SecurityInfoProvider(
        transport=lambda *_: trends_payload(), now=lambda: NOW + timedelta(minutes=5)
    )
    result = provider.intraday("SH:600519")
    assert result.is_stale and result.quote.is_stale
    assert result.error


def test_intraday_service_reuses_inflight_result_and_stale_cache_then_discards_old_day() -> None:
    """并发读合并、断网保留当日真缓存，跨天不冒充今日时序。"""
    clock = [NOW]
    failing = [False]
    calls = []

    def transport(*_):
        """由测试控制网络故障并记录调用数。"""
        calls.append(1)
        if failing[0]:
            raise TimeoutError("连接超时")
        return trends_payload()

    async def scenario():
        """在同一事件循环中验证并发、降级与跨天行为。"""
        service = IntradayService(SecurityInfoProvider(transport=transport, now=lambda: clock[0]))
        first, second = await asyncio.gather(service.read("SH:600519"), service.read("SH:600519"))
        assert first == second and len(calls) == 1
        clock[0] += timedelta(seconds=3)
        failing[0] = True
        fallback = await service.read("SH:600519")
        assert (
            fallback.points == first.points and fallback.quote.last_price == first.quote.last_price
        )
        assert fallback.is_stale and fallback.quote.is_stale and fallback.fetched_at == NOW
        retry = await service.read("SH:600519")
        assert retry == fallback and len(calls) == 2
        clock[0] += timedelta(days=1)
        tomorrow = await service.read("SH:600519")
        assert tomorrow.points == () and tomorrow.quote is None
        assert tomorrow.trading_date == "2026-11-18" and tomorrow.is_stale

    asyncio.run(scenario())


@pytest.mark.parametrize("body", [b"[]", b'{"data":NaN}', b"x" * (2 * 1024 * 1024 + 1)])
def test_provider_rejects_invalid_nonfinite_or_oversize_payload(body) -> None:
    """恶劣上游响应不能成为行情或证券结果。"""
    provider = SecurityInfoProvider(transport=lambda *_: body, now=lambda: NOW)
    with pytest.raises(ValueError):
        provider.intraday("SH:600519")


def test_intraday_cache_size_is_bounded() -> None:
    """不同证券访问不能无限扩大内存缓存。"""

    def transport(request, _timeout):
        """为请求证券生成隔离的固定分钟数据。"""
        code = parse_qs(urlparse(request.full_url).query)["secid"][0].split(".")[1]
        return trends_payload(code=code)

    async def scenario():
        """依次读取两个证券并断言最旧缓存被移除。"""
        service = IntradayService(
            SecurityInfoProvider(transport=transport, now=lambda: NOW), max_cached=1
        )
        await service.read("SH:600519")
        await service.read("SH:600000")
        assert list(service._cache) == ["SH:600000"]
        assert list(service._attempted_at) == ["SH:600000"]

    asyncio.run(scenario())
