from __future__ import annotations

from datetime import datetime, timezone

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.api.deps import get_db  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import create_engine_for_url  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture
def api_client():
    engine = create_engine_for_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    application = create_app(create_tables=False)

    def override_db():
        with factory() as session:
            yield session

    application.dependency_overrides[get_db] = override_db
    with TestClient(application) as client:
        yield client


def test_api_buy_sell_idempotency_and_error_contract(api_client) -> None:
    security = api_client.post(
        "/api/v1/securities", json={"market": "SH", "symbol": "600519", "name": "Moutai"}
    ).json()
    buy_payload = {
        "account_id": "account-default",
        "security_id": security["id"],
        "executed_at": datetime(2026, 11, 1, tzinfo=timezone.utc).isoformat(),
        "qty": "1000",
        "price": "10",
        "fees": {"commission": "1.00"},
        "client_request_id": "buy-1",
    }
    # An omitted account is the supported default-account path.
    buy_payload.pop("account_id")
    first = api_client.post("/api/v1/trades/buy", json=buy_payload)
    assert first.status_code == 201
    duplicate = api_client.post("/api/v1/trades/buy", json=buy_payload)
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == first.json()["id"]

    sell = api_client.post(
        "/api/v1/trades/sell",
        json={
            "security_id": security["id"],
            "executed_at": datetime(2026, 11, 5, tzinfo=timezone.utc).isoformat(),
            "qty": "1000",
            "price": "11",
            "client_request_id": "sell-1",
        },
    )
    assert sell.status_code == 201
    too_much = api_client.post(
        "/api/v1/trades/sell",
        json={
            "security_id": security["id"],
            "executed_at": datetime(2026, 11, 6, tzinfo=timezone.utc).isoformat(),
            "qty": "1",
            "price": "11",
        },
    )
    assert too_much.status_code in {409, 422}
    assert too_much.json()["error"]["code"] in {"INSUFFICIENT_LOT_QTY", "VALIDATION_ERROR"}

