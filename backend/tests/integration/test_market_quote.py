from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.session import create_engine_for_url  # noqa: E402
from app.domain.enums import CalcBasis  # noqa: E402
from app.domain.models import Fees  # noqa: E402
from app.market_data import (  # noqa: E402
    AShareTradingCalendar,
    MockQuoteProvider,
    QuoteCollector,
    QuoteService,
    QuoteSnapshot,
)
from app.models.ledger import Account, ProfitTarget, QuoteCache, Security, Strategy  # noqa: E402
from app.services.ledger import TradeLedgerService  # noqa: E402
from app.services.target import TargetService  # noqa: E402


def _session() -> Session:
    engine = create_engine_for_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)()


def test_calendar_sessions_and_quote_snapshot_change() -> None:
    calendar = AShareTradingCalendar()
    assert calendar.is_session_open(datetime(2026, 11, 5, 10, 0, tzinfo=calendar.timezone))
    assert not calendar.is_session_open(datetime(2026, 11, 5, 12, 0, tzinfo=calendar.timezone))
    snapshot = QuoteSnapshot("sh:600519", "11", prev_close="10")
    assert snapshot.change_pct == Decimal("10.000000")


def test_quote_refresh_triggers_target_only_for_fresh_data() -> None:
    session = _session()
    security = Security(market="SH", symbol="600519", name="Test")
    account = Account(name="A")
    session.add_all([security, account])
    session.flush()
    strategy = Strategy(account_id=account.id, name="DEFAULT")
    session.add(strategy)
    session.flush()
    buy = TradeLedgerService(session).buy(
        account_id=account.id,
        security_id=security.id,
        strategy_id=strategy.id,
        executed_at=datetime.now(timezone.utc),
        qty="100",
        price="10",
        fees=Fees(),
    )
    lot = buy.lot
    assert lot is not None
    target = ProfitTarget(
        lot_id=lot.id,
        planned_sell_qty=Decimal("100"),
        target_return_pct=Decimal("0.1"),
        calculation_basis=CalcBasis.GROSS.value,
        target_price_estimate=TargetService.solve_price(
            unit_cost=lot.unit_cost,
            planned_sell_qty="100",
            target_return_pct="0.1",
            basis=CalcBasis.GROSS,
        ),
        alert_enabled=True,
        status="ACTIVE",
    )
    session.add(target)
    session.commit()
    provider = MockQuoteProvider({"SH:600519": {"last_price": "11", "prev_close": "10"}})
    collector = QuoteCollector(provider, QuoteService())
    asyncio.run(collector.refresh(session, [security]))
    session.refresh(target)
    assert target.status == "TRIGGERED"

    target.status = "ACTIVE"
    target.triggered_at = None
    session.commit()
    provider.set_quotes(
        {
            "SH:600519": {
                "last_price": "20",
                "prev_close": "10",
                "fetched_at": datetime(2020, 1, 1, tzinfo=timezone.utc),
            }
        }
    )
    collector.last_refresh_at = None
    asyncio.run(collector.refresh(session, [security]))
    session.refresh(target)
    assert target.status == "ACTIVE"
    provider.set_quotes(
        {
            "SH:600519": {
                "last_price": "20",
                "prev_close": "10",
                "fetched_at": datetime.now(timezone.utc),
                "is_stale": True,
            }
        }
    )
    collector.last_refresh_at = None
    asyncio.run(collector.refresh(session, [security]))
    session.refresh(target)
    assert target.status == "ACTIVE"
    session.close()


def test_failed_refresh_keeps_last_price_and_marks_cache_stale() -> None:
    session = _session()
    security = Security(market="SH", symbol="600519", name="Test")
    session.add(security)
    session.commit()

    class FailingProvider(MockQuoteProvider):
        def get_quotes(self, symbols):
            raise TimeoutError("upstream unavailable")

    provider = MockQuoteProvider({"SH:600519": {"last_price": "11", "prev_close": "10"}})
    collector = QuoteCollector(provider, QuoteService(), min_refresh_interval_seconds=0)
    first_at = datetime(2026, 11, 5, 10, 0, tzinfo=timezone.utc)
    asyncio.run(collector.refresh(session, [security], now=first_at))

    collector.provider = FailingProvider()
    fallback = asyncio.run(
        collector.refresh(session, [security], now=first_at.replace(hour=10, minute=1))
    )

    assert fallback[0].last_price == Decimal("11.000000")
    assert fallback[0].is_stale is True
    cached = session.get(QuoteCache, security.id)
    assert cached is not None and cached.last_price == Decimal("11.000000")
    assert cached.is_stale is True
    session.close()


def test_partial_refresh_returns_full_scope_and_marks_omissions_stale() -> None:
    session = _session()
    first_security = Security(market="SH", symbol="600519", name="First")
    second_security = Security(market="SZ", symbol="002594", name="Second")
    session.add_all([first_security, second_security])
    session.commit()
    first_at = datetime(2026, 11, 16, 2, 0, tzinfo=timezone.utc)
    second_at = first_at.replace(minute=5)
    provider = MockQuoteProvider(
        {
            "SH:600519": QuoteSnapshot(
                "SH:600519", "11", prev_close="10", fetched_at=first_at
            ),
            "SZ:002594": QuoteSnapshot(
                "SZ:002594", "21", prev_close="20", fetched_at=first_at
            ),
        }
    )
    collector = QuoteCollector(provider, QuoteService(), min_refresh_interval_seconds=0)

    asyncio.run(
        collector.refresh(
            session, [first_security, second_security], now=first_at
        )
    )
    collector.provider = MockQuoteProvider(
        {
            "SH:600519": QuoteSnapshot(
                "SH:600519", "12", prev_close="10", fetched_at=second_at
            )
        }
    )
    result = asyncio.run(
        collector.refresh(
            session, [first_security, second_security], now=second_at
        )
    )
    by_symbol = {snapshot.symbol: snapshot for snapshot in result}

    assert by_symbol["SH:600519"].last_price == Decimal("12.000000")
    assert by_symbol["SH:600519"].is_stale is False
    assert by_symbol["SZ:002594"].last_price == Decimal("21.000000")
    assert by_symbol["SZ:002594"].is_stale is True
    assert collector.breaker.failures == 0
    session.close()


def test_refresh_does_not_stale_unsupported_security_cache() -> None:
    session = _session()
    ashare = Security(market="SH", symbol="600519", name="A-share")
    us_security = Security(market="US", symbol="AAPL", name="US equity")
    session.add_all([ashare, us_security])
    session.flush()
    now = datetime(2026, 11, 16, 2, 0, tzinfo=timezone.utc)
    session.add(
        QuoteCache(
            security_id=us_security.id,
            last_price=Decimal("200"),
            prev_close=Decimal("198"),
            fetched_at=now,
            provider="mock-us",
            is_stale=False,
        )
    )
    session.commit()

    class AShareOnlyProvider(MockQuoteProvider):
        supports_symbol = staticmethod(lambda symbol: symbol.startswith("SH:"))

        def __init__(self) -> None:
            super().__init__(
                {
                    "SH:600519": QuoteSnapshot(
                        "SH:600519", "12", prev_close="10", fetched_at=now
                    )
                }
            )
            self.requested: list[str] = []

        def get_quotes(self, symbols):
            self.requested.extend(symbols)
            return super().get_quotes(symbols)

    provider = AShareOnlyProvider()
    collector = QuoteCollector(
        provider,
        QuoteService(),
        min_refresh_interval_seconds=0,
    )
    result = asyncio.run(
        collector.refresh(
            session,
            [ashare, us_security],
            now=now,
        )
    )
    by_symbol = {snapshot.symbol: snapshot for snapshot in result}
    us_cache = session.get(QuoteCache, us_security.id)

    assert provider.requested == ["SH:600519"]
    assert by_symbol["US:AAPL"].is_stale is False
    assert us_cache is not None and us_cache.last_price == Decimal("200")
    assert us_cache.is_stale is False
    session.close()


def test_empty_provider_result_keeps_failure_state_and_stale_cache() -> None:
    session = _session()
    security = Security(market="SH", symbol="600519", name="Test")
    session.add(security)
    session.commit()
    started = datetime(2026, 11, 16, 2, 0, tzinfo=timezone.utc)
    provider = MockQuoteProvider(
        {
            "SH:600519": QuoteSnapshot(
                "SH:600519", "11", prev_close="10", fetched_at=started
            )
        }
    )
    collector = QuoteCollector(provider, QuoteService(), min_refresh_interval_seconds=0)
    asyncio.run(collector.refresh(session, [security], now=started))
    collector.provider = MockQuoteProvider()

    for minute in range(1, 4):
        current = started.replace(minute=minute)
        result = asyncio.run(collector.refresh(session, [security], now=current))
        assert result[0].last_price == Decimal("11.000000")
        assert result[0].is_stale is True

    assert collector.breaker.failures == 3
    assert collector.breaker.is_open_at(started.replace(minute=3, second=20)) is True
    session.close()

