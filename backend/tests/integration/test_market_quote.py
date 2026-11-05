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

