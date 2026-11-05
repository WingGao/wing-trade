from __future__ import annotations

from datetime import datetime, timezone

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")

from app.db.base import Base  # noqa: E402
from app.db.session import create_engine_for_url  # noqa: E402
from app.models.ledger import Account, AuditEvent, Security, Strategy  # noqa: E402
from app.services.ledger import TradeLedgerService  # noqa: E402


def test_buy_sell_lifecycle() -> None:
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    engine = create_engine_for_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        account = Account(name="main")
        security = Security(market="SH", symbol="600519", name="Kweichow Moutai")
        session.add_all([account, security])
        session.flush()
        strategy = Strategy(account_id=account.id, name="DEFAULT")
        session.add(strategy)
        session.flush()
        service = TradeLedgerService(session)
        buy = service.buy(
            account_id=account.id, security_id=security.id, strategy_id=strategy.id,
            executed_at=datetime(2026, 11, 1, tzinfo=timezone.utc), qty="1000", price="10",
        )
        sell = service.sell(
            account_id=account.id, security_id=security.id, strategy_id=strategy.id,
            executed_at=datetime(2026, 11, 5, tzinfo=timezone.utc), qty="1000", price="11",
        )
        assert buy.lot is not None
        assert len(sell.matches) == 1
        events = list(session.scalars(select(AuditEvent)))
        assert any(event.entity_type == "LOT" and event.action == "LOT_CREATED" for event in events)
        assert any(event.entity_type == "CYCLE" and event.action == "CYCLE_CREATED" for event in events)
        assert any(event.entity_type == "LOT" and event.action == "LOT_UPDATED" for event in events)

        service.void_trade(sell.id)
        events = list(session.scalars(select(AuditEvent)))
        assert sum(event.entity_type == "LOT" and event.action == "LOT_UPDATED" for event in events) >= 2
        assert any(event.entity_type == "CYCLE" and event.action == "CYCLE_UPDATED" for event in events)
