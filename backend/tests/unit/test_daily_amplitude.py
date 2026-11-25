"""近 20 个已收盘交易日平均振幅拉取、计算与缓存服务单元测试。"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from urllib.request import Request

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.market_data.amplitude import (
    DailyAmplitudeProvider,
    DailyAmplitudeService,
)
from app.market_data.collector import QuoteCollector
from app.market_data.provider import MockQuoteProvider
from app.models.ledger import Account, Base, Security, SecurityDailyMetric, Strategy
from app.services.dashboard import DashboardService
from app.services.ledger import TradeLedgerService


def _build_eastmoney_kline_payload(klines: list[str]) -> bytes:
    """构造东方财富日 K 线 JSON 响应字节串。"""
    return json.dumps({"rc": 0, "data": {"klines": klines}}).encode("utf-8")


def _generate_trading_dates(end_day: date, count: int) -> list[date]:
    """生成以 end_day 结尾的 count 个工作日列表（升序）。"""
    days: list[date] = []
    cursor = end_day
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    days.reverse()
    return days


def test_ashare_excludes_today_intraday_bar_and_averages_20_closed_days() -> None:
    """盘中拉取 A 股日 K 时必须排除当日未收盘 K 线，并精确平均最近 20 个已收盘交易日。"""
    # 2026-11-25 为周三，北京时间 10:30（UTC 02:30）处于盘中未收盘。
    trading_days = _generate_trading_dates(date(2026, 11, 25), 22)
    klines: list[str] = []
    for idx, day in enumerate(trading_days):
        # 2026-11-25 当日盘中振幅设为 99.99%，若未排除会显著拉高均值；
        # 其余 21 个历史交易日振幅分别为 1.00%, 2.00%, ..., 21.00%（最近 20 个已收盘为 2.00..21.00，均值 11.50%）。
        amp = "99.99" if day == date(2026, 11, 25) else f"{idx + 1}.00"
        klines.append(f"{day.isoformat()},10.00,10.20,10.50,9.80,1000,10000,{amp}")

    requested_urls: list[str] = []

    def fake_transport(request: Request, _timeout: float) -> bytes:
        requested_urls.append(request.full_url)
        return _build_eastmoney_kline_payload(klines)

    provider = DailyAmplitudeProvider(transport=fake_transport)
    now_intraday = datetime(2026, 11, 25, 2, 30, tzinfo=UTC)  # 10:30 CST
    snap = provider.fetch_avg_amplitude("SH:600519", now=now_intraday)

    assert snap is not None
    assert snap.symbol == "SH:600519"
    assert snap.sample_days == 20
    assert snap.as_of_date == "2026-11-24"
    assert snap.avg_amplitude_20d == Decimal("11.500000")
    assert snap.provider == "eastmoney-kline"
    assert len(requested_urls) == 1
    assert "secid=1.600519" in requested_urls[0]

    # 收盘后（北京时间 15:05 = UTC 07:05）应纳入 2026-11-25。
    now_after_close = datetime(2026, 11, 25, 7, 5, tzinfo=UTC)
    snap_closed = provider.fetch_avg_amplitude("SH:600519", now=now_after_close)
    assert snap_closed is not None
    assert snap_closed.as_of_date == "2026-11-25"
    assert snap_closed.sample_days == 20
    assert snap_closed.avg_amplitude_20d > Decimal("15.000000")


def test_ashare_fallback_formula_when_amplitude_field_missing_and_fewer_than_20_days() -> None:
    """当上游缺少 f58 振幅字段且不足 20 个交易日时，按 (高-低)/昨收×100% 对实际天数求均值。"""
    klines = [
        "2026-11-20,10.00,10.00,10.20,9.90,100,1000",  # 首日无昨收
        "2026-11-23,10.10,10.20,10.50,10.00,100,1000",  # (10.50 - 10.00) / 10.00 * 100 = 5.00%
        "2026-11-24,10.20,10.00,10.40,9.89,100,1000",  # (10.40 - 9.89) / 10.20 * 100 = 5.00%
    ]
    provider = DailyAmplitudeProvider(
        transport=lambda _req, _timeout: _build_eastmoney_kline_payload(klines)
    )
    snap = provider.fetch_avg_amplitude("SZ:300750", now=datetime(2026, 11, 25, 2, 0, tzinfo=UTC))

    assert snap is not None
    assert snap.sample_days == 2
    assert snap.as_of_date == "2026-11-24"
    assert snap.avg_amplitude_20d == Decimal("5.000000")


def test_us_stock_falls_back_to_tencent_kline_and_skips_open_fund() -> None:
    """美股东财日 K 不可用时自动回退腾讯美股日 K；场外基金 OF 直接跳过不请求公网。"""
    calls: list[str] = []

    def fake_transport(request: Request, _timeout: float) -> bytes:
        calls.append(request.full_url)
        if "push2his.eastmoney.com" in request.full_url or "searchapi.eastmoney.com" in request.full_url:
            raise OSError("eastmoney unreachable")
        # 腾讯美股日 K 返回 3 根 K 线，产生 2 个有效振幅日（均为 4.00%）
        payload = {
            "data": {
                "usVOO": {
                    "qfqday": [
                        ["2026-11-20", "100.00", "100.00", "101.00", "99.00", "1000"],
                        ["2026-11-23", "100.00", "100.00", "102.00", "98.00", "1000"],
                        ["2026-11-24", "100.00", "100.00", "103.00", "99.00", "1000"],
                    ]
                }
            }
        }
        return json.dumps(payload).encode("utf-8")

    provider = DailyAmplitudeProvider(transport=fake_transport)
    assert provider.supports_symbol("OF:000300") is False
    assert provider.fetch_avg_amplitude("OF:000300") is None
    assert calls == []

    # 美东 2026-11-25 10:00（UTC 15:00）盘中，截止 2026-11-24
    snap = provider.fetch_avg_amplitude("US:VOO", now=datetime(2026, 11, 25, 15, 0, tzinfo=UTC))
    assert snap is not None
    assert snap.provider == "tencent-us-kline"
    assert snap.sample_days == 2
    assert snap.as_of_date == "2026-11-24"
    assert snap.avg_amplitude_20d == Decimal("4.000000")


def test_service_caching_cooldown_and_dashboard_integration() -> None:
    """验证 DB 缓存命中、失败冷却、mock 离线模式与 DashboardService 字段集成。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    with SessionLocal() as session:
        account = Account(name="主账户", currency="CNY")
        session.add(account)
        session.flush()
        strategy = Strategy(account_id=account.id, name="DEFAULT")
        sec_stock = Security(market="SH", symbol="600519", name="贵州茅台", security_type="STOCK")
        sec_fund = Security(market="OF", symbol="000300", name="沪深300联接", security_type="FUND")
        session.add_all([strategy, sec_stock, sec_fund])
        session.flush()

        ledger = TradeLedgerService(session)
        ledger.buy(
            account_id=account.id,
            security_id=sec_stock.id,
            strategy_id=strategy.id,
            executed_at=datetime(2026, 11, 10, 2, 0, tzinfo=UTC),
            qty=Decimal("100"),
            price=Decimal("1500"),
        )
        ledger.buy(
            account_id=account.id,
            security_id=sec_fund.id,
            strategy_id=strategy.id,
            executed_at=datetime(2026, 11, 10, 2, 0, tzinfo=UTC),
            qty=Decimal("1000"),
            price=Decimal("1.5000"),
        )
        session.commit()

        fetch_count = 0
        trading_days = _generate_trading_dates(date(2026, 11, 24), 20)
        klines = [
            f"{d.isoformat()},10.00,10.10,10.30,9.95,1000,10000,3.50"
            for d in trading_days
        ]

        def fake_transport(_req: Request, _timeout: float) -> bytes:
            nonlocal fetch_count
            fetch_count += 1
            return _build_eastmoney_kline_payload(klines)

        provider = DailyAmplitudeProvider(transport=fake_transport)
        amp_service = DailyAmplitudeService(provider=provider, network_enabled=True)
        now = datetime(2026, 11, 25, 2, 30, tzinfo=UTC)  # 10:30 CST

        collector = QuoteCollector(
            MockQuoteProvider(
                {
                    "SH:600519": {"last_price": Decimal("1520"), "prev_close": Decimal("1500")},
                    "OF:000300": {"last_price": Decimal("1.5200"), "prev_close": Decimal("1.5000")},
                }
            )
        )
        dashboard_service = DashboardService(collector, amplitude_service=amp_service)

        # 第一次请求（auto）：触发拉取并写入 security_daily_metrics
        result1 = asyncio.run(
            dashboard_service.build(session, account_id=account.id, mode="auto", now=now)
        )
        assert fetch_count == 1
        by_symbol = {f"{p['market']}:{p['symbol']}": p for p in result1["positions"]}
        assert by_symbol["SH:600519"]["avgAmplitude20d"] == Decimal("3.500000")
        assert by_symbol["SH:600519"]["avgAmplitudeDays"] == 20
        assert by_symbol["SH:600519"]["avgAmplitudeAsOf"] == "2026-11-24"
        assert by_symbol["OF:000300"]["avgAmplitude20d"] is None
        assert by_symbol["OF:000300"]["avgAmplitudeDays"] is None
        assert by_symbol["OF:000300"]["avgAmplitudeAsOf"] is None

        cached_row = session.get(SecurityDailyMetric, sec_stock.id)
        assert cached_row is not None
        assert cached_row.avg_amplitude_20d == Decimal("3.500000")

        # 第二次请求（同一已收盘交易日内）：直接命中 DB 缓存，不再触发上游请求
        result2 = asyncio.run(
            dashboard_service.build(session, account_id=account.id, mode="auto", now=now)
        )
        assert fetch_count == 1
        by_symbol2 = {f"{p['market']}:{p['symbol']}": p for p in result2["positions"]}
        assert by_symbol2["SH:600519"]["avgAmplitude20d"] == Decimal("3.500000")


def test_service_failure_cooldown_prevents_repeated_upstream_calls() -> None:
    """上游拉取失败时平滑降级返回空，且冷却期内不再重复请求上游。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    calls = 0

    def failing_transport(_req: Request, _timeout: float) -> bytes:
        nonlocal calls
        calls += 1
        raise OSError("network down")

    with SessionLocal() as session:
        sec = Security(market="SH", symbol="600000", name="浦发银行", security_type="STOCK")
        session.add(sec)
        session.commit()

        service = DailyAmplitudeService(
            provider=DailyAmplitudeProvider(transport=failing_transport),
            network_enabled=True,
            attempt_cooldown_seconds=1800,
        )
        now = datetime(2026, 11, 25, 2, 0, tzinfo=UTC)
        res1 = asyncio.run(service.get_or_refresh(session, [sec], allow_refresh=True, now=now))
        assert res1 == {}
        assert calls == 1

        # 5 分钟内再次调用，命中冷却期，不重复打上游
        res2 = asyncio.run(
            service.get_or_refresh(
                session, [sec], allow_refresh=True, now=now + timedelta(minutes=5)
            )
        )
        assert res2 == {}
        assert calls == 1
