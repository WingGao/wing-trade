from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_db
from app.api.v1 import market
from app.db.base import Base
from app.db.session import create_engine_for_url
from app.main import create_app
from app.market_data.security_info import IntradayService, SecurityInfoProvider


@pytest.fixture
def market_client(monkeypatch):
    """只使用内存账本和注入行情传输，不访问用户数据库与真实网络。"""
    engine = create_engine_for_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    clock = [datetime(2026, 11, 17, 2, 30, tzinfo=UTC)]
    failures = {"lookup": False, "trends": False}
    calls = []

    def transport(request, _timeout):
        """提供真实响应结构并允许测试独立模拟查名和分钟故障。"""
        lookup = "suggest/get" in request.full_url
        name = "lookup" if lookup else "trends"
        calls.append(name)
        if failures[name]:
            raise TimeoutError("offline")
        payload = (
            {
                "QuotationCodeTable": {
                    "Data": [
                        {"Code": "600519", "Name": "贵州茅台", "MktNum": "1"},
                    ]
                }
            }
            if lookup
            else {
                "data": {
                    "code": "600519",
                    "market": 1,
                    "preClose": "10",
                    "trends": ["2026-11-17 10:30,10,12.345,10,10,0,0,10"],
                }
            }
        )
        return json.dumps(payload, ensure_ascii=False).encode()

    provider = SecurityInfoProvider(transport=transport, now=lambda: clock[0])
    monkeypatch.setattr(market, "security_info_provider", provider)
    monkeypatch.setattr(market, "intraday_service", IntradayService(provider))
    application = create_app(create_tables=False)

    def override_db():
        """每个 HTTP 请求独立会话，保持外键和事务行为真实。"""
        with factory() as session:
            yield session

    application.dependency_overrides[get_db] = override_db
    with TestClient(application) as client:
        yield client, failures, calls, clock
    engine.dispose()


def test_lookup_uses_external_name_without_creating_local_rows(market_client) -> None:
    """每次显式查找调用外部接口，查找本身不创建数据库证券。"""
    client, _, calls, _ = market_client
    for _ in range(2):
        response = client.get("/api/v1/securities/lookup?query=600519")
        assert response.status_code == 200
        assert response.json()["securities"][0]["name"] == "贵州茅台"
        assert response.json()["securities"][0]["id"] is None
    assert calls == ["lookup", "lookup"]
    assert client.get("/api/v1/securities").json() == []


def test_lookup_failure_preserves_local_result_and_error(market_client) -> None:
    """外部断网不使已有本地证券无法查询。"""
    client, failures, _, _ = market_client
    security = client.post(
        "/api/v1/securities",
        json={
            "market": "SH",
            "symbol": "600519",
            "name": "贵州茅台",
        },
    ).json()
    failures["lookup"] = True
    result = client.get("/api/v1/securities/lookup?query=600519").json()
    assert result["error"] and result["securities"][0]["id"] == security["id"]


def test_intraday_updates_same_quote_used_for_lot_valuation_and_marks_failure(
    market_client,
) -> None:
    """图价与批次缓存同源，断网后保留原价格且明确标记过期。"""
    client, failures, _, clock = market_client
    security = client.post(
        "/api/v1/securities",
        json={
            "market": "SH",
            "symbol": "600519",
            "name": "贵州茅台",
        },
    ).json()
    buy = client.post(
        "/api/v1/trades/buy",
        json={
            "securityId": security["id"],
            "executedAt": "2026-11-16T02:00:00Z",
            "qty": "100",
            "price": "10",
        },
    )
    assert buy.status_code == 201
    path = f"/api/v1/securities/{security['id']}"
    response = client.get(path + "/intraday")
    assert response.status_code == 200
    assert response.json()["points"][0]["price"] == "12.345000"
    assert response.json()["quote"]["lastPrice"] == "12.345000"
    lots = client.get(path + "/lots").json()
    assert lots["quote"]["lastPrice"] == "12.345000"
    assert lots["lots"][0]["unrealizedPnl"] == "234.5000"
    failures["trends"] = True
    clock[0] += timedelta(seconds=3)
    failed = client.get(path + "/intraday").json()
    assert failed["isStale"] and failed["error"]
    assert failed["points"] == response.json()["points"]
    assert client.get(path + "/lots").json()["quote"]["isStale"]


def test_intraday_missing_security_and_no_quote_failure(market_client) -> None:
    """未知证券为404，行情首读失败为空状态且不捏造零价格。"""
    client, failures, _, _ = market_client
    assert client.get("/api/v1/securities/missing/intraday").status_code == 404
    security = client.post(
        "/api/v1/securities",
        json={
            "market": "SH",
            "symbol": "600519",
            "name": "贵州茅台",
        },
    ).json()
    failures["trends"] = True
    response = client.get(f"/api/v1/securities/{security['id']}/intraday")
    assert response.status_code == 200
    data = response.json()
    assert data["points"] == [] and data["quote"] is None
    assert data["isStale"] and data["error"]
