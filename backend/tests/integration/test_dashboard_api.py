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


def test_last_sell_price_across_cycles_and_after_void(
    dashboard_client, monkeypatch
) -> None:
    """验证 dashboard 与 lots 接口返回跨周期最近一笔有效卖出价，撤销后自动回退。"""
    router_module = import_module("app.api.v1.router")

    security = dashboard_client.post(
        "/api/v1/securities",
        json={"market": "SZ", "symbol": "300750", "name": "宁德时代"},
    ).json()
    # 第一轮建仓并清仓卖出（264.50）
    assert dashboard_client.post(
        "/api/v1/trades/buy",
        json={
            "securityId": security["id"],
            "executedAt": datetime(2026, 10, 1, 2, 0, tzinfo=UTC).isoformat(),
            "qty": "100",
            "price": "240",
        },
    ).status_code == 201
    first_sell = dashboard_client.post(
        "/api/v1/trades/sell",
        json={
            "securityId": security["id"],
            "executedAt": datetime(2026, 10, 5, 2, 0, tzinfo=UTC).isoformat(),
            "qty": "100",
            "price": "264.5",
        },
    )
    assert first_sell.status_code == 201

    # 第二轮重新建仓（当前周期无卖出），应仍可查到上轮最近卖出价 264.5
    assert dashboard_client.post(
        "/api/v1/trades/buy",
        json={
            "securityId": security["id"],
            "executedAt": datetime(2026, 11, 1, 2, 0, tzinfo=UTC).isoformat(),
            "qty": "200",
            "price": "242",
        },
    ).status_code == 201

    now = datetime.now(UTC)
    collector = QuoteCollector(
        MockQuoteProvider(
            {
                "SZ:300750": QuoteSnapshot(
                    "SZ:300750",
                    "248.6",
                    prev_close="251.4",
                    source_time=now,
                    fetched_at=now,
                )
            }
        ),
        min_refresh_interval_seconds=0,
    )
    monkeypatch.setattr(router_module, "quote_collector", collector)

    dash = dashboard_client.get("/api/v1/dashboard?mode=manual").json()
    pos = dash["positions"][0]
    assert pos["lastSellPrice"] == "264.500000"
    assert pos["lastSellExecutedAt"] is not None
    assert pos["vsLastSellPct"] == "-6.011342"

    lots_resp = dashboard_client.get(f"/api/v1/securities/{security['id']}/lots").json()
    assert lots_resp["summary"]["lastSellPrice"] == "264.500000"
    assert lots_resp["summary"]["lastSellExecutedAt"] is not None
    assert lots_resp["summary"]["vsLastSellPct"] == "-6.011342"

    # 再卖出一笔更新的价格（270），应切换为 270
    second_sell = dashboard_client.post(
        "/api/v1/trades/sell",
        json={
            "securityId": security["id"],
            "executedAt": datetime(2026, 11, 10, 2, 0, tzinfo=UTC).isoformat(),
            "qty": "100",
            "price": "270",
        },
    )
    assert second_sell.status_code == 201
    second_sell_id = second_sell.json()["id"]

    dash_after_second = dashboard_client.get("/api/v1/dashboard?mode=manual").json()
    assert dash_after_second["positions"][0]["lastSellPrice"] == "270.000000"

    # 撤销第二笔卖出后，自动回退为第一笔卖出 264.5
    assert dashboard_client.post(
        f"/api/v1/trades/{second_sell_id}/void",
        json={"reason": "测试撤销回退"},
    ).status_code == 200
    dash_after_void = dashboard_client.get("/api/v1/dashboard?mode=manual").json()
    assert dash_after_void["positions"][0]["lastSellPrice"] == "264.500000"
    assert dash_after_void["positions"][0]["vsLastSellPct"] == "-6.011342"
