from __future__ import annotations

from datetime import UTC, datetime, timezone
from decimal import Decimal

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
from tests.auth_helpers import attach_default_admin_auth  # noqa: E402


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
    with factory() as setup_session:
        attach_default_admin_auth(application, setup_session)
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


def test_us_account_isolated_currency_and_security_flow(api_client) -> None:
    account = api_client.post(
        "/api/v1/accounts", json={"name": "US Trading", "currency": "USD"}
    )
    assert account.status_code == 201
    account_id = account.json()["id"]
    security = api_client.post(
        "/api/v1/securities", json={"market": "US", "symbol": "AAPL", "name": "Apple"}
    )
    assert security.status_code == 201
    assert security.json()["currency"] == "USD"

    buy = api_client.post(
        "/api/v1/trades/buy",
        json={
            "accountId": account_id,
            "securityId": security.json()["id"],
            "executedAt": datetime(2026, 11, 5, tzinfo=timezone.utc).isoformat(),
            "qty": "2",
            "price": "200",
            "clientRequestId": "us-buy-1",
        },
    )
    assert buy.status_code == 201
    assert api_client.get(f"/api/v1/analytics/summary?accountId={account_id}").json()["currency"] == "USD"

    mismatch = api_client.post(
        "/api/v1/trades/buy",
        json={
            "securityId": security.json()["id"],
            "executedAt": datetime(2026, 11, 5, tzinfo=timezone.utc).isoformat(),
            "qty": "1",
            "price": "200",
        },
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "ACCOUNT_CURRENCY_MISMATCH"


def test_lifo_preview_returns_real_buy_details_and_confirms_same_lots(api_client) -> None:
    """工作台预览应使用最近买入，并让正式卖出扣减同一组明确批次。"""
    security = api_client.post(
        "/api/v1/securities", json={"market": "SH", "symbol": "600000", "name": "浦发银行"}
    ).json()
    older_buy = api_client.post(
        "/api/v1/trades/buy",
        json={
            "securityId": security["id"],
            "executedAt": datetime(2026, 11, 1, tzinfo=UTC).isoformat(),
            "qty": "10",
            "price": "10",
            "fees": {"commission": "1"},
            "clientRequestId": "lifo-older-buy",
        },
    )
    newer_buy = api_client.post(
        "/api/v1/trades/buy",
        json={
            "securityId": security["id"],
            "executedAt": datetime(2026, 11, 2, tzinfo=UTC).isoformat(),
            "qty": "4",
            "price": "20",
            "fees": {"commission": "2"},
            "clientRequestId": "lifo-newer-buy",
        },
    )
    assert older_buy.status_code == 201
    assert newer_buy.status_code == 201
    lots = api_client.get(f"/api/v1/securities/{security['id']}/lots").json()["lots"]
    older_lot = next(lot for lot in lots if lot["buyTradeId"] == older_buy.json()["id"])
    newer_lot = next(lot for lot in lots if lot["buyTradeId"] == newer_buy.json()["id"])

    preview_payload = {
        "securityId": security["id"],
        "executedAt": datetime(2026, 11, 5, tzinfo=UTC).isoformat(),
        "qty": "6",
        "price": "30",
    }
    fifo_preview = api_client.post("/api/v1/trades/sell/preview", json=preview_payload)
    assert fifo_preview.status_code == 200
    assert fifo_preview.json()["matches"][0]["lotId"] == older_lot["id"]

    preview = api_client.post(
        "/api/v1/trades/sell/preview",
        json={**preview_payload, "matchingMethod": "LIFO"},
    )
    assert preview.status_code == 200
    body = preview.json()
    assert body["currency"] == "CNY"
    assert [match["lotId"] for match in body["matches"]] == [newer_lot["id"], older_lot["id"]]
    assert [match["matchedQty"] for match in body["matches"]] == ["4.0000", "2.0000"]
    matched_qty = sum(
        (Decimal(match["matchedQty"]) for match in body["matches"]),
        Decimal("0"),
    )
    assert matched_qty == Decimal("6")
    newer_match = body["matches"][0]
    assert newer_match["buyExecutedAt"] == "2026-11-02T08:00:00+08:00"
    assert newer_match["buyPrice"] == "20.000000"
    assert newer_match["buyQty"] == "4.0000"
    assert newer_match["availableQty"] == "4.0000"
    assert newer_match["allocatedBuyCost"] == "82.0000"

    sell = api_client.post(
        "/api/v1/trades/sell",
        json={
            **preview_payload,
            "qty": body["qty"],
            "price": body["price"],
            "clientRequestId": "lifo-preview-sell",
            "matches": [
                {"lotId": match["lotId"], "matchedQty": match["matchedQty"]}
                for match in body["matches"]
            ],
        },
    )
    assert sell.status_code == 201
    assert [match["lotId"] for match in sell.json()["matches"]] == [
        newer_lot["id"],
        older_lot["id"],
    ]
    assert all(match["matchingMethod"] == "MANUAL" for match in sell.json()["matches"])
    preview_by_lot = {match["lotId"]: match for match in body["matches"]}
    for match in sell.json()["matches"]:
        expected = preview_by_lot[match["lotId"]]
        for field in ("matchedQty", "allocatedBuyCost", "allocatedSellNet", "realizedPnl"):
            assert match[field] == expected[field]

    expected_realized = sum(
        (Decimal(match["realizedPnl"]) for match in sell.json()["matches"]),
        Decimal("0"),
    )
    positions = api_client.get("/api/v1/positions").json()
    security_position = next(
        position for position in positions if position["securityId"] == security["id"]
    )
    assert security_position["remainingQty"] == "8.0000"
    assert security_position["realizedPnl"] == f"{expected_realized:.4f}"

    remaining = api_client.get(
        f"/api/v1/securities/{security['id']}/lots?includeClosed=true"
    ).json()
    assert remaining["closedLotCount"] == 1
    remaining_by_id = {lot["id"]: lot["remainingQty"] for lot in remaining["lots"]}
    assert remaining_by_id[older_lot["id"]] == "8.0000"
    assert remaining_by_id[newer_lot["id"]] == "0.0000"


def test_api_enforces_timezone_and_returns_beijing_time(api_client) -> None:
    """API 入参必须携带显式时区，且所有对外时间响应统一输出带 +08:00 的北京时间。"""
    security = api_client.post(
        "/api/v1/securities", json={"market": "SH", "symbol": "600519", "name": "贵州茅台"}
    ).json()

    # 1. 无时区入参必须被拒绝（422）
    naive_buy = api_client.post(
        "/api/v1/trades/buy",
        json={
            "securityId": security["id"],
            "executedAt": "2026-11-25T09:30:00",
            "qty": "10",
            "price": "1500",
        },
    )
    assert naive_buy.status_code == 422

    # 2. 带 +08:00 或 Z 时区入参均成功，且回显统一为 +08:00 北京时间
    beijing_buy = api_client.post(
        "/api/v1/trades/buy",
        json={
            "securityId": security["id"],
            "executedAt": "2026-11-25T09:30:00+08:00",
            "qty": "10",
            "price": "1500",
            "clientRequestId": "tz-buy-beijing",
        },
    )
    assert beijing_buy.status_code == 201
    buy_body = beijing_buy.json()
    assert buy_body["executedAt"] == "2026-11-25T09:30:00+08:00"
    assert buy_body["createdAt"].endswith("+08:00")

    # 3. 编辑交易无时区入参被拒绝；带 Z 入参正确换算为 +08:00；未改动保持 revision 不变
    naive_patch = api_client.patch(
        f"/api/v1/trades/{buy_body['id']}",
        json={"revision": 1, "executedAt": "2026-11-25T10:00:00"},
    )
    assert naive_patch.status_code == 422

    same_time_patch = api_client.patch(
        f"/api/v1/trades/{buy_body['id']}",
        json={"revision": 1, "executedAt": "2026-11-25T01:30:00Z"},
    )
    assert same_time_patch.status_code == 200
    assert same_time_patch.json()["trade"]["revision"] == 1
    assert same_time_patch.json()["trade"]["executedAt"] == "2026-11-25T09:30:00+08:00"

    # 4. 看板 asOf 与交易列表均返回 +08:00
    dashboard = api_client.get("/api/v1/dashboard").json()
    assert dashboard["asOf"].endswith("+08:00")
    trades = api_client.get("/api/v1/trades").json()
    assert all(row["executedAt"].endswith("+08:00") for row in trades)
    assert all(row["createdAt"].endswith("+08:00") for row in trades)


