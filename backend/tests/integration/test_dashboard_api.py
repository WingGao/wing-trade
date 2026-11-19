from __future__ import annotations

from datetime import UTC, datetime
from importlib import import_module

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.api.deps import get_db  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import create_engine_for_url  # noqa: E402
from app.main import create_app  # noqa: E402
from app.market_data import MockQuoteProvider, QuoteCollector, QuoteSnapshot  # noqa: E402
from tests.auth_helpers import attach_default_admin_auth  # noqa: E402


@pytest.fixture
def dashboard_client():
    engine = create_engine_for_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    application = create_app(create_tables=False)

    def override_db():
        with factory() as session:
            yield session

    application.dependency_overrides[get_db] = override_db
    with factory() as setup_session:
        attach_default_admin_auth(application, setup_session)
    with TestClient(application) as client:
        yield client


def test_dashboard_api_serializes_consistent_camel_case_payload(
    dashboard_client, monkeypatch
) -> None:
    router_module = import_module("app.api.v1.router")

    security = dashboard_client.post(
        "/api/v1/securities",
        json={"market": "SH", "symbol": "600519", "name": "Moutai"},
    ).json()
    buy = dashboard_client.post(
        "/api/v1/trades/buy",
        json={
            "securityId": security["id"],
            "executedAt": datetime(2026, 11, 1, tzinfo=UTC).isoformat(),
            "qty": "100",
            "price": "10",
        },
    )
    assert buy.status_code == 201
    now = datetime.now(UTC)
    collector = QuoteCollector(
        MockQuoteProvider(
            {
                "SH:600519": QuoteSnapshot(
                    "SH:600519",
                    "12",
                    prev_close="10",
                    source_time=now,
                    fetched_at=now,
                )
            }
        ),
        min_refresh_interval_seconds=0,
    )
    monkeypatch.setattr(router_module, "quote_collector", collector)

    response = dashboard_client.get("/api/v1/dashboard?mode=manual")

    assert response.status_code == 200
    payload = response.json()
    assert payload["accountId"]
    assert payload["currency"] == "CNY"
    assert payload["marketSessionOpen"] in {True, False}
    assert payload["pollAfterSeconds"] in {10, 60}
    assert payload["positions"][0]["unrealizedPnl"] == "200.0000"
    assert payload["positions"][0]["unrealizedReturnPct"] == "20.000000"
    assert payload["positions"][0]["quote"]["lastPrice"] == "12.000000"
    assert payload["positions"][0]["quote"]["changePct"] == "20.000000"
    assert payload["positions"][0]["lowestPriceGroup"]["lotCount"] == 1
    assert payload["summary"]["valuationStatus"] == "fresh"
    assert payload["summary"]["unrealizedPnl"] == "200.0000"
    assert payload["quotes"][0]["securityId"] == security["id"]


def test_dashboard_api_missing_quote_uses_null_not_zero(
    dashboard_client, monkeypatch
) -> None:
    router_module = import_module("app.api.v1.router")

    security = dashboard_client.post(
        "/api/v1/securities",
        json={"market": "SZ", "symbol": "002594", "name": "BYD"},
    ).json()
    dashboard_client.post(
        "/api/v1/trades/buy",
        json={
            "securityId": security["id"],
            "executedAt": datetime(2026, 11, 1, tzinfo=UTC).isoformat(),
            "qty": "100",
            "price": "10",
        },
    )
    monkeypatch.setattr(
        router_module,
        "quote_collector",
        QuoteCollector(MockQuoteProvider(), min_refresh_interval_seconds=0),
    )

    payload = dashboard_client.get("/api/v1/dashboard?mode=cache").json()

    assert payload["positions"][0]["quote"] is None
    assert payload["positions"][0]["unrealizedPnl"] is None
    assert payload["positions"][0]["lowestPriceGroup"]["unrealizedPnl"] is None
    assert payload["summary"]["valuationStatus"] == "unavailable"
    assert payload["summary"]["unrealizedPnl"] is None
    assert payload["quotes"][0]["lastPrice"] is None
    assert payload["quotes"][0]["isStale"] is True
