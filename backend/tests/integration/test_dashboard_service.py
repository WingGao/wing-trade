from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.session import create_engine_for_url  # noqa: E402
from app.domain.models import Fees, MatchRequest  # noqa: E402
from app.market_data import (  # noqa: E402
    MockQuoteProvider,
    QuoteCollector,
    QuoteService,
    QuoteSnapshot,
)
from app.models.ledger import Account, QuoteCache, Security, Strategy  # noqa: E402
from app.services.dashboard import DashboardService  # noqa: E402
from app.services.ledger import TradeLedgerService  # noqa: E402


def _session() -> Session:
    engine = create_engine_for_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)()


def _account_with_strategy(
    session: Session, *, currency: str = "CNY", strategy_name: str = "DEFAULT"
) -> tuple[Account, Strategy]:
    account = Account(name=f"Account {currency}", currency=currency)
    session.add(account)
    session.flush()
    strategy = Strategy(account_id=account.id, name=strategy_name)
    session.add(strategy)
    session.flush()
    return account, strategy


def _buy(
    session: Session,
    account: Account,
    security: Security,
    strategy: Strategy,
    *,
    price: str,
    qty: str,
    day: int,
    fees: Fees | None = None,
):
    return TradeLedgerService(session).buy(
        account_id=account.id,
        security_id=security.id,
        strategy_id=strategy.id,
        executed_at=datetime(2026, 11, day, 2, 0, tzinfo=UTC),
        qty=qty,
        price=price,
        fees=fees or Fees(),
        commit=False,
    )


def _dashboard(
    session: Session,
    account: Account,
    provider: MockQuoteProvider,
    *,
    now: datetime,
    mode: str = "auto",
    include_closed: bool = False,
    collector: QuoteCollector | None = None,
) -> dict:
    active_collector = collector or QuoteCollector(
        provider,
        QuoteService(),
        min_refresh_interval_seconds=10,
    )
    return asyncio.run(
        DashboardService(active_collector).build(
            session,
            account_id=account.id,
            include_closed=include_closed,
            mode=mode,
            now=now,
        )
    )


def test_dashboard_merges_lowest_price_lots_and_keeps_scopes_isolated() -> None:
    session = _session()
    account, strategy = _account_with_strategy(session)
    other_strategy = Strategy(account_id=account.id, name="OTHER")
    other_account, other_account_strategy = _account_with_strategy(session)
    security = Security(market="SH", symbol="600519", name="Kweichow Moutai")
    session.add_all([other_strategy, security])
    session.flush()
    _buy(session, account, security, strategy, price="8", qty="100", day=1)
    _buy(session, account, security, strategy, price="8", qty="50", day=2)
    _buy(session, account, security, strategy, price="10", qty="100", day=3)
    _buy(
        session,
        account,
        security,
        other_strategy,
        price="8",
        qty="20",
        day=1,
    )
    _buy(
        session,
        other_account,
        security,
        other_account_strategy,
        price="8",
        qty="999",
        day=1,
    )
    session.commit()
    now = datetime(2026, 11, 16, 2, 30, tzinfo=UTC)
    provider = MockQuoteProvider(
        {
            "SH:600519": QuoteSnapshot(
                "SH:600519",
                "10",
                prev_close="9.5",
                fetched_at=now,
            )
        }
    )

    dashboard = _dashboard(session, account, provider, now=now)
    positions = {
        position["strategyId"]: position for position in dashboard["positions"]
    }
    target = positions[strategy.id]

    assert len(dashboard["positions"]) == 2
    assert target["remainingQty"] == Decimal("250.0000")
    assert target["unrealizedPnl"] == Decimal("300.0000")
    assert target["unrealizedReturnPct"] == Decimal("13.636364")
    assert target["lowestPriceGroup"] == {
        "purchasePrice": Decimal("8.000000"),
        "lotCount": 2,
        "remainingQty": Decimal("150.0000"),
        "remainingCost": Decimal("1200.0000"),
        "unrealizedPnl": Decimal("300.0000"),
        "unrealizedReturnPct": Decimal("25.000000"),
    }
    assert dashboard["summary"]["valuationStatus"] == "fresh"
    assert dashboard["summary"]["unrealizedPnl"] == Decimal("340.0000")
    session.close()


def test_partial_quotes_make_summary_unavailable_and_keep_group_info() -> None:
    session = _session()
    account, strategy = _account_with_strategy(session)
    first = Security(market="SH", symbol="600519", name="First")
    second = Security(market="SZ", symbol="002594", name="Second")
    session.add_all([first, second])
    session.flush()
    _buy(session, account, first, strategy, price="10", qty="100", day=1)
    _buy(session, account, second, strategy, price="20", qty="50", day=2)
    session.commit()
    now = datetime(2026, 11, 16, 2, 30, tzinfo=UTC)
    provider = MockQuoteProvider(
        {
            "SH:600519": QuoteSnapshot(
                "SH:600519", "12", prev_close="10", fetched_at=now
            )
        }
    )

    dashboard = _dashboard(session, account, provider, now=now)
    missing = next(
        position
        for position in dashboard["positions"]
        if position["securityId"] == second.id
    )

    assert dashboard["summary"]["valuationStatus"] == "partial"
    assert dashboard["summary"]["unrealizedPnl"] is None
    assert dashboard["summary"]["unrealizedReturnPct"] is None
    assert dashboard["summary"]["quotedOpenPositions"] == 1
    assert missing["quote"] is None
    assert missing["lowestPriceGroup"]["unrealizedPnl"] is None
    assert missing["lowestPriceGroup"]["remainingQty"] == Decimal("50.0000")
    session.close()


def test_complete_stale_quotes_are_reference_valuation_and_empty_is_zero() -> None:
    session = _session()
    account, strategy = _account_with_strategy(session)
    security = Security(market="SH", symbol="600519", name="Test")
    session.add(security)
    session.flush()
    _buy(session, account, security, strategy, price="10", qty="10", day=1)
    now = datetime(2026, 11, 16, 2, 30, tzinfo=UTC)
    session.add(
        QuoteCache(
            security_id=security.id,
            last_price=Decimal("11"),
            prev_close=Decimal("10"),
            fetched_at=now - timedelta(minutes=1),
            source_time=now - timedelta(minutes=2),
            provider="mock",
            is_stale=False,
        )
    )
    session.commit()

    stale = _dashboard(
        session,
        account,
        MockQuoteProvider(),
        now=now,
        mode="cache",
    )
    assert stale["summary"]["valuationStatus"] == "stale"
    assert stale["summary"]["unrealizedPnl"] == Decimal("10.0000")
    assert stale["positions"][0]["quote"]["isStale"] is True

    empty_account, _ = _account_with_strategy(session)
    session.commit()
    empty = _dashboard(
        session,
        empty_account,
        MockQuoteProvider(),
        now=now,
        mode="cache",
    )
    assert empty["summary"]["valuationStatus"] == "empty"
    assert empty["summary"]["unrealizedPnl"] == Decimal("0")
    assert empty["summary"]["unrealizedReturnPct"] is None
    session.close()


def test_invalid_cached_price_is_unavailable_instead_of_zero() -> None:
    session = _session()
    account, strategy = _account_with_strategy(session)
    security = Security(market="SH", symbol="600519", name="Test")
    session.add(security)
    session.flush()
    _buy(session, account, security, strategy, price="10", qty="10", day=1)
    now = datetime(2026, 11, 16, 2, 30, tzinfo=UTC)
    session.add(
        QuoteCache(
            security_id=security.id,
            last_price=Decimal("0"),
            fetched_at=now,
            provider="legacy",
            is_stale=False,
        )
    )
    session.commit()

    dashboard = _dashboard(
        session,
        account,
        MockQuoteProvider(),
        now=now,
        mode="cache",
    )

    assert dashboard["positions"][0]["quote"] is None
    assert dashboard["positions"][0]["unrealizedPnl"] is None
    assert dashboard["summary"]["valuationStatus"] == "unavailable"
    assert dashboard["summary"]["unrealizedPnl"] is None
    session.close()


def test_cache_auto_and_manual_modes_share_throttle_and_session_schedule() -> None:
    session = _session()
    account, strategy = _account_with_strategy(session)
    security = Security(market="SH", symbol="600519", name="Test")
    session.add(security)
    session.flush()
    _buy(session, account, security, strategy, price="10", qty="10", day=1)
    session.commit()
    open_now = datetime(2026, 11, 16, 2, 30, tzinfo=UTC)
    closed_now = open_now.replace(hour=10, minute=30)

    class CountingProvider(MockQuoteProvider):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def get_quotes(self, symbols):
            self.calls += 1
            return {
                symbol: QuoteSnapshot(
                    symbol,
                    "11",
                    prev_close="10",
                    fetched_at=self.value_time,
                )
                for symbol in symbols
            }

    provider = CountingProvider()
    provider.value_time = open_now
    collector = QuoteCollector(
        provider,
        QuoteService(),
        min_refresh_interval_seconds=10,
    )
    cached = _dashboard(
        session, account, provider, now=open_now, mode="cache", collector=collector
    )
    assert provider.calls == 0
    assert cached["summary"]["valuationStatus"] == "unavailable"
    assert cached["pollAfterSeconds"] == 10

    automatic = _dashboard(
        session, account, provider, now=open_now, mode="auto", collector=collector
    )
    assert provider.calls == 1
    assert automatic["summary"]["valuationStatus"] == "fresh"

    provider.value_time = closed_now
    _dashboard(
        session,
        account,
        provider,
        now=closed_now,
        mode="manual",
        collector=collector,
    )
    assert provider.calls == 2
    refreshed = _dashboard(
        session,
        account,
        provider,
        now=closed_now,
        mode="auto",
        collector=collector,
    )
    assert provider.calls == 2
    assert refreshed["pollAfterSeconds"] == 60
    session.close()


def test_closed_auto_refresh_waits_one_hour_after_stale_failure() -> None:
    session = _session()
    account, strategy = _account_with_strategy(session)
    security = Security(market="SH", symbol="600519", name="Test")
    session.add(security)
    session.flush()
    _buy(session, account, security, strategy, price="10", qty="10", day=1)
    closed_now = datetime(2026, 11, 16, 10, 30, tzinfo=UTC)
    session.add(
        QuoteCache(
            security_id=security.id,
            last_price=Decimal("11"),
            prev_close=Decimal("10"),
            fetched_at=closed_now - timedelta(hours=2),
            provider="mock",
            is_stale=False,
        )
    )
    session.commit()

    class EmptyProvider(MockQuoteProvider):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def get_quotes(self, symbols):
            self.calls += 1
            return {}

    provider = EmptyProvider()
    collector = QuoteCollector(
        provider,
        QuoteService(),
        min_refresh_interval_seconds=0,
    )

    first = _dashboard(
        session,
        account,
        provider,
        now=closed_now,
        mode="auto",
        collector=collector,
    )
    within_hour = _dashboard(
        session,
        account,
        provider,
        now=closed_now + timedelta(minutes=1),
        mode="auto",
        collector=collector,
    )
    next_hour = _dashboard(
        session,
        account,
        provider,
        now=closed_now + timedelta(minutes=61),
        mode="auto",
        collector=collector,
    )

    assert provider.calls == 2
    assert first["summary"]["valuationStatus"] == "stale"
    assert within_hour["summary"]["valuationStatus"] == "stale"
    assert next_hour["summary"]["valuationStatus"] == "stale"
    assert first["summary"]["unrealizedPnl"] == Decimal("10.0000")
    session.close()


def test_include_closed_keeps_trade_after_reload() -> None:
    session = _session()
    account, strategy = _account_with_strategy(session)
    security = Security(market="SH", symbol="600519", name="Test")
    session.add(security)
    session.flush()
    buy = _buy(session, account, security, strategy, price="10", qty="100", day=1)
    session.commit()
    now = datetime(2026, 11, 16, 2, 30, tzinfo=UTC)
    provider = MockQuoteProvider(
        {
            "SH:600519": QuoteSnapshot(
                "SH:600519", "11", prev_close="10", fetched_at=now
            )
        }
    )
    before = _dashboard(session, account, provider, now=now, mode="auto")
    assert before["summary"]["unrealizedPnl"] == Decimal("100.0000")

    lot = buy.lot
    assert lot is not None
    TradeLedgerService(session).sell(
        account_id=account.id,
        security_id=security.id,
        strategy_id=strategy.id,
        executed_at=datetime(2026, 11, 17, 2, 0, tzinfo=UTC),
        qty="100",
        price="11",
        matches=[MatchRequest(lot.id, "100")],
        fees=Fees(),
        commit=False,
    )
    session.commit()

    open_only = _dashboard(
        session, account, provider, now=now + timedelta(days=1), mode="cache"
    )
    with_closed = _dashboard(
        session,
        account,
        provider,
        now=now + timedelta(days=1),
        mode="cache",
        include_closed=True,
    )
    assert open_only["summary"]["valuationStatus"] == "empty"
    assert with_closed["summary"]["valuationStatus"] == "empty"
    assert with_closed["positions"][0]["remainingQty"] == Decimal("0.0000")
    assert with_closed["positions"][0]["lowestPriceGroup"] is None
    session.close()
