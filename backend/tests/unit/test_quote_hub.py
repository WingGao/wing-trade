"""MarketDataHub 单元测试：覆盖广播、基金估算联动、聚合、落盘与引用计数。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.market_data.calendar import AShareTradingCalendar
from app.market_data.hub import MarketDataHub
from app.market_data.provider import MockQuoteProvider, QuoteSnapshot
from app.market_data.service import QuoteService
from app.models.auth import User
from app.models.ledger import Account, QuoteCache, Security, WatchlistItem


def _make_memory_db():
    """创建独立内存 SQLite 会话工厂。"""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def test_hub_publish_derives_of_proxy_and_coalesces_broadcast() -> None:
    """发布参考标的报价时应立即在内存中派生场外基金估算净值，并在聚合窗口内合并广播。"""
    engine, SessionFactory = _make_memory_db()
    now = datetime(2026, 11, 25, 2, 0, tzinfo=UTC)  # 上海 10:00 交易时段
    try:
        with SessionFactory() as session:
            user = User(id="u1", email="u1@example.com", role="user", status="active")
            acc = Account(id="a1", user_id="u1", name="默认账户", currency="CNY")
            proxy = Security(
                id="s-etf",
                market="SH",
                symbol="510300",
                name="沪深300ETF",
                security_type="ETF",
            )
            fund = Security(
                id="s-of",
                market="OF",
                symbol="000300",
                name="沪深300联接",
                security_type="FUND",
                proxy_symbol="SH:510300",
                proxy_name="沪深300ETF",
                tracking_ratio=Decimal("0.950000"),
            )
            session.add_all([user, acc, proxy, fund])
            session.flush()
            session.add(WatchlistItem(account_id="a1", security_id="s-of", sort_order=0))
            session.commit()

        quote_service = QuoteService(AShareTradingCalendar())
        hub = MarketDataHub(
            quote_service=quote_service,
            provider=MockQuoteProvider(),
            session_factory=SessionFactory,
            broadcast_coalesce_seconds=0.05,
            now=lambda: now,
        )
        hub.warm_from_db()

        # 先注入基金昨日官方净值（2026-11-24 15:00 CST = 07:00 UTC）
        official_time = datetime(2026, 11, 24, 7, 0, tzinfo=UTC)
        hub.publish(
            {
                "OF:000300": QuoteSnapshot(
                    symbol="OF:000300",
                    last_price=Decimal("2.0000"),
                    prev_close=Decimal("1.9800"),
                    source_time=official_time,
                    fetched_at=now,
                    provider="eastmoney-fund",
                )
            }
        )

        async def _exercise() -> dict[str, object]:
            subscriber, initial = hub.subscribe_client("u1")
            assert initial["quotes"][0]["symbol"] == "OF:000300"
            assert initial["quotes"][0]["isEstimated"] is False

            # 在 50ms 窗口内连续推送两次 ETF 报价，应合并为一帧且联动派生基金估算净值
            hub.publish(
                {
                    "SH:510300": QuoteSnapshot(
                        symbol="SH:510300",
                        last_price=Decimal("4.0400"),
                        prev_close=Decimal("4.0000"),
                        change_pct=Decimal("1.000000"),
                        source_time=now,
                        fetched_at=now,
                        provider="longbridge",
                    )
                }
            )
            hub.publish(
                {
                    "SH:510300": QuoteSnapshot(
                        symbol="SH:510300",
                        last_price=Decimal("4.0800"),
                        prev_close=Decimal("4.0000"),
                        change_pct=Decimal("2.000000"),
                        source_time=now,
                        fetched_at=now,
                        provider="longbridge",
                    )
                }
            )
            frame = await asyncio.wait_for(subscriber.queue.get(), timeout=1.0)
            hub.unsubscribe_client(subscriber.subscriber_id)
            return frame

        event = asyncio.run(_exercise())
        assert event["event"] == "quote"
        data = event["data"]
        assert isinstance(data, dict)
        quotes = data["quotes"]
        # 用户仅关注 OF:000300，因此只收到派生后的 OF:000300（2.0000 * (1 + 2% * 0.95) = 2.038000）
        assert len(quotes) == 1
        assert quotes[0]["symbol"] == "OF:000300"
        assert quotes[0]["isEstimated"] is True
        assert quotes[0]["lastPrice"] == "2.038000"
        assert quotes[0]["officialNav"] == "2.000000"

        # 落盘只写原始报价（OF 官方净值与 ETF 价格），不把估算值污染到 quote_cache
        flushed = hub.flush_dirty_to_db()
        assert flushed == 2
        with SessionFactory() as session:
            of_row = session.get(QuoteCache, "s-of")
            etf_row = session.get(QuoteCache, "s-etf")
            assert of_row is not None and of_row.last_price == Decimal("2.000000")
            assert etf_row is not None and etf_row.last_price == Decimal("4.080000")
    finally:
        engine.dispose()


def test_hub_ephemeral_subscription_reference_counting() -> None:
    """多个 SSE 客户端临时关注同一标的时应维护引用计数，最后一个断开才清理。"""
    engine, SessionFactory = _make_memory_db()
    try:
        with SessionFactory() as session:
            session.add_all(
                [
                    User(id="u1", email="u1@example.com", role="user", status="active"),
                    User(id="u2", email="u2@example.com", role="user", status="active"),
                ]
            )
            session.commit()

        hub = MarketDataHub(
            quote_service=QuoteService(),
            provider=MockQuoteProvider(),
            session_factory=SessionFactory,
            broadcast_coalesce_seconds=0.0,
        )
        sub1, _ = hub.subscribe_client("u1", extra_symbols=["SH:600519"])
        sub2, _ = hub.subscribe_client("u2", extra_symbols=["SH:600519"])
        assert "SH:600519" in hub.desired_symbols()

        hub.unsubscribe_client(sub1.subscriber_id)
        assert "SH:600519" in hub.desired_symbols()

        hub.unsubscribe_client(sub2.subscriber_id)
        assert "SH:600519" not in hub.desired_symbols()
    finally:
        engine.dispose()
