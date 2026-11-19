"""交易编辑、范围状态和显式重算使用临时账本验证，不触碰真实 ledger.db。"""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.session import create_engine_for_url
from app.main import create_app
from app.models import (
    Account,
    AuditEvent,
    Base,
    Lot,
    PositionRefreshState,
    Security,
    Strategy,
    Trade,
)
from app.models.ledger import Cycle, ProfitTarget, TradeMatch
from app.services.ledger import TradeLedgerService
from app.services.position_refresh import PositionRefreshService

Context = tuple[Session, Account, Security, Strategy]


@pytest.fixture
def context(tmp_path: Path) -> Generator[Context, None, None]:
    """每个用例独占文件数据库，支持关闭会话后再次读取验证持久性。"""
    engine = create_engine_for_url(f"sqlite:///{(tmp_path / 'ledger-test.sqlite').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        account = Account(name="临时测试账户", currency="CNY")
        security = Security(market="SH", symbol="600519", name="贵州茅台")
        session.add_all([account, security])
        session.flush()
        strategy = Strategy(account_id=account.id, name="DEFAULT")
        session.add(strategy)
        session.commit()
        yield session, account, security, strategy
    engine.dispose()


def client_for(session: Session, *, raise_server_exceptions: bool = True) -> TestClient:
    """HTTP 依赖只返回当前临时会话，应用启动不创建默认真实账本。"""
    from tests.auth_helpers import attach_default_admin_auth

    application = create_app(create_tables=False)

    def override_db() -> Generator[Session, None, None]:
        """使 API 写入与测试断言使用同一隔离事务环境。"""
        yield session

    application.dependency_overrides[get_db] = override_db
    attach_default_admin_auth(application, session)
    return TestClient(application, raise_server_exceptions=raise_server_exceptions)


def buy(context: Context, *, qty: str = "10", price: str = "10") -> Trade:
    """建立有完整来源和幂等标识的有效买入事实。"""
    session, account, security, strategy = context
    return TradeLedgerService(session).buy(
        account_id=account.id,
        security_id=security.id,
        strategy_id=strategy.id,
        executed_at=datetime(2026, 11, 1, 1, tzinfo=UTC),
        qty=qty,
        price=price,
        client_request_id=f"buy-{strategy.id}-{security.id}",
        external_trade_id="external-1",
        import_batch_id="batch-1",
        memo="原始备注",
    )


def sell(context: Context, *, qty: str = "6") -> Trade:
    """在同范围记录可用于成本、顺序及结清场景的有效卖出。"""
    session, account, security, strategy = context
    return TradeLedgerService(session).sell(
        account_id=account.id,
        security_id=security.id,
        strategy_id=strategy.id,
        executed_at=datetime(2026, 11, 2, 1, tzinfo=UTC),
        qty=qty,
        price="15",
    )


def scope_url(context: Context, suffix: str) -> str:
    """明确传递测试范围，避免默认账户或默认策略掩盖隔离错误。"""
    _, account, security, strategy = context
    return (
        f"/api/v1/securities/{security.id}/{suffix}?accountId={account.id}&strategyId={strategy.id}"
    )


def projection(session: Session) -> dict[str, list[tuple[object, ...]]]:
    """捕获全部派生账务值和显式止盈，验证失败时没有部分重建。"""
    return {
        model.__tablename__: sorted(
            tuple(str(getattr(row, column.name)) for column in model.__table__.columns)
            for row in session.scalars(select(model))
        )
        for model in (Lot, TradeMatch, Cycle, ProfitTarget)
    }


def audit_count(session: Session) -> int:
    """读取审计数量，识别无变化或失败提交是否产生副作用。"""
    return session.scalar(select(func.count()).select_from(AuditEvent)) or 0


def test_edit_preserves_identity_precision_audit_and_old_projection(context: Context) -> None:
    """一次更正全部字段，同时保留原 ID、来源、幂等键和未刷新的批次。"""
    session, account, security, strategy = context
    trade = buy(context)
    original = projection(session)
    request_id = trade.client_request_id
    payload = {
        "revision": 1,
        "executedAt": "2026-11-01T09:00:01.123456+08:00",
        "qty": "10.1234",
        "price": "11.123456",
        "commission": "1.1000",
        "stampDuty": "2.2000",
        "transferFee": "3.3000",
        "otherFee": "4.4000",
        "memo": "更正备注",
        "reason": "核对交割单",
    }
    with client_for(session) as client:
        response = client.patch(f"/api/v1/trades/{trade.id}", json=payload)
        assert response.status_code == 200, response.text
        body = response.json()
        updated = body["trade"]
        assert body["needsRefresh"] is True
        assert updated["id"] == trade.id
        assert updated["revision"] == 2
        assert updated["accountId"] == account.id
        assert updated["securityId"] == security.id
        assert updated["strategyId"] == strategy.id
        assert updated["side"] == "BUY"
        assert updated["source"] == "MANUAL"
        assert updated["externalTradeId"] == "external-1"
        assert updated["importBatchId"] == "batch-1"
        assert updated["clientRequestId"] == request_id
        assert updated["qty"] == "10.1234"
        assert updated["price"] == "11.123456"
        assert Decimal(updated["grossAmount"]) == Decimal("112.6072")
        assert updated["feesTotal"] == "11.0000"
        assert updated["memo"] == "更正备注"
        assert projection(session) == original
        event = session.scalar(select(AuditEvent).where(AuditEvent.action == "TRADE_UPDATED"))
        assert event is not None and event.reason == "核对交割单"
        before, after = json.loads(event.before_json), json.loads(event.after_json)
        assert before["price"] == "10.000000" and before["revision"] == 1
        assert after["price"] == "11.123456" and after["revision"] == 2
        assert after["executed_at"] == "2026-11-01T01:00:01.123456+00:00"


def test_noop_and_memo_clear_keep_original_state_semantics(context: Context) -> None:
    """等值规整不写版本、审计或状态；仅清空备注仍按要求标记待刷新。"""
    session, _, _, _ = context
    trade = buy(context)
    baseline = audit_count(session)
    with client_for(session) as client:
        result = client.patch(
            f"/api/v1/trades/{trade.id}",
            json={
                "revision": 1,
                "qty": "10.00000",
                "price": "10.0000000",
                "executedAt": "2026-11-01T09:00:00+08:00",
                "commission": "0.00000",
            },
        )
        assert result.status_code == 200, result.text
        assert result.json()["trade"]["revision"] == 1
        assert result.json()["needsRefresh"] is False
        assert audit_count(session) == baseline
        assert session.scalar(select(func.count()).select_from(PositionRefreshState)) == 0
        cleared = client.patch(f"/api/v1/trades/{trade.id}", json={"revision": 1, "memo": None})
        assert cleared.status_code == 200
        assert cleared.json()["trade"]["memo"] is None
        assert cleared.json()["needsRefresh"] is True
        again = client.patch(f"/api/v1/trades/{trade.id}", json={"revision": 2, "memo": None})
        assert again.json()["trade"]["revision"] == 2
        assert again.json()["needsRefresh"] is True
        assert audit_count(session) == baseline + 1


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"revision": True},
        {"revision": 0},
        {"revision": 1, "qty": None},
        {"revision": 1, "executedAt": None},
        {"revision": 1, "commission": None},
        {"revision": 1, "price": "0"},
        {"revision": 1, "qty": "-1"},
        {"revision": 1, "price": "NaN"},
        {"revision": 1, "otherFee": "Infinity"},
        {"revision": 1, "stampDuty": "-0.01"},
        {"revision": 1, "transferFee": 0.1},
        {"revision": 1, "executedAt": "invalid"},
        {"revision": 1, "accountId": "other"},
        {"revision": 1, "securityId": "other"},
        {"revision": 1, "strategyId": "other"},
        {"revision": 1, "side": "SELL"},
        {"revision": 1, "source": "IMPORT"},
        {"revision": 1, "grossAmount": "0"},
        {"revision": 1, "clientRequestId": "changed"},
    ],
)
def test_invalid_or_forbidden_patch_has_no_partial_writes(context: Context, payload: dict) -> None:
    """非法精度、空值或越权字段统一拒绝，交易、审计与状态均保持原样。"""
    session, _, _, _ = context
    trade = buy(context)
    original = projection(session)
    baseline = audit_count(session)
    with client_for(session) as client:
        result = client.patch(f"/api/v1/trades/{trade.id}", json=payload)
        assert result.status_code == 422, result.text
        assert result.json()["error"]["code"] == "VALIDATION_ERROR"
    session.refresh(trade)
    assert trade.revision == 1 and trade.price == Decimal("10")
    assert audit_count(session) == baseline
    assert projection(session) == original
    assert session.scalar(select(func.count()).select_from(PositionRefreshState)) == 0


def test_stale_revision_voided_and_missing_trade_are_explicit(context: Context) -> None:
    """拒绝过期表单与撤销交易，并保持稳定的冲突和缺失错误码。"""
    session, _, _, _ = context
    trade = buy(context)
    with client_for(session) as client:
        assert (
            client.patch(
                f"/api/v1/trades/{trade.id}",
                json={
                    "revision": 1,
                    "price": "11",
                },
            ).status_code
            == 200
        )
        conflict = client.patch(
            f"/api/v1/trades/{trade.id}",
            json={
                "revision": 1,
                "price": "12",
            },
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "TRADE_REVISION_CONFLICT"
        assert trade.price == Decimal("11")
        assert client.post(f"/api/v1/trades/{trade.id}/void").status_code == 200
        voided = client.patch(f"/api/v1/trades/{trade.id}", json={"revision": 3, "price": "12"})
        assert voided.status_code == 409
        assert voided.json()["error"]["code"] == "TRADE_NOT_EDITABLE"
        missing = client.patch("/api/v1/trades/not-present", json={"revision": 1})
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "TRADE_NOT_FOUND"


@pytest.mark.parametrize("changes", [{"qty": "5"}, {"executedAt": "2026-11-03T01:00:00Z"}])
def test_failed_recalculation_retains_edit_old_projection_and_target(
    context: Context,
    changes: dict,
) -> None:
    """合法单笔更正可造成历史冲突；重算失败必须保留修改事实与原全部派生值。"""
    session, _, _, _ = context
    trade = buy(context)
    sell(context)
    session.add(
        ProfitTarget(
            lot_id=trade.lot.id,
            target_return_pct=Decimal("0.1"),
            planned_sell_qty=Decimal("1"),
            calculation_basis="NET",
            status="ACTIVE",
        )
    )
    session.commit()
    original = projection(session)
    with client_for(session) as client:
        edited = client.patch(f"/api/v1/trades/{trade.id}", json={"revision": 1, **changes})
        assert edited.status_code == 200
        failure = client.post(scope_url(context, "recalculate"))
        assert failure.status_code == 409, failure.text
        assert failure.json()["error"]["code"] == "INSUFFICIENT_LOT_QTY"
        session.expire_all()
        assert session.get(Trade, trade.id).revision == 2
        assert projection(session) == original
        assert client.get(scope_url(context, "lots")).json()["needsRefresh"] is True
        corrected = client.patch(
            f"/api/v1/trades/{trade.id}",
            json={
                "revision": 2,
                "qty": "10",
                "executedAt": "2026-11-01T01:00:00Z",
            },
        )
        assert corrected.status_code == 200
        assert client.post(scope_url(context, "recalculate")).status_code == 200
        assert client.get(scope_url(context, "lots")).json()["needsRefresh"] is False


def test_refresh_rebuilds_cost_and_only_clears_target_scope(context: Context) -> None:
    """修改后成本和收益由既有重算更新，其他账户、策略和个股标记不受影响。"""
    session, account, security, strategy = context
    trade = buy(context)
    sell_trade = sell(context)
    other_account = Account(name="其他账户", currency="CNY")
    other_security = Security(market="SZ", symbol="000001", name="平安银行")
    other_strategy = Strategy(account_id=account.id, name="其他策略")
    session.add_all([other_account, other_security, other_strategy])
    session.flush()
    foreign_strategy = Strategy(account_id=other_account.id, name="DEFAULT")
    session.add(foreign_strategy)
    session.flush()
    others = [
        (account.id, security.id, other_strategy.id),
        (other_account.id, security.id, foreign_strategy.id),
        (account.id, other_security.id, strategy.id),
    ]
    for account_id, security_id, strategy_id in others:
        PositionRefreshService.mark(
            session, account_id=account_id, security_id=security_id, strategy_id=strategy_id
        )
    session.commit()
    with client_for(session) as client:
        assert (
            client.patch(
                f"/api/v1/trades/{trade.id}",
                json={
                    "revision": 1,
                    "price": "11",
                },
            ).status_code
            == 200
        )
        # 卖出更正同样只修改事实，直到显式刷新才影响配对收益。
        assert (
            client.patch(
                f"/api/v1/trades/{sell_trade.id}",
                json={
                    "revision": 1,
                    "price": "16",
                    "commission": "1",
                },
            ).status_code
            == 200
        )
        updated_at = trade.updated_at
        response = client.post(scope_url(context, "recalculate"))
        assert response.status_code == 200, response.text
        refreshed = client.get(scope_url(context, "lots")).json()
        assert refreshed["needsRefresh"] is False
        assert refreshed["lots"][0]["remainingQty"] == "4.0000"
        assert refreshed["lots"][0]["remainingCost"] == "44.0000"
        assert refreshed["realizedPnl"] == "29.0000"
        assert trade.revision == 2
        assert trade.updated_at.replace(tzinfo=None) == updated_at.replace(tzinfo=None)
        for account_id, security_id, strategy_id in others:
            assert PositionRefreshService.needs_refresh(
                session, account_id=account_id, security_id=security_id, strategy_id=strategy_id
            )
        assert client.post(scope_url(context, "recalculate")).status_code == 200
        assert session.scalar(select(func.count()).select_from(Trade)) == 2
        # 重算后再编辑必须重新置真，不能被前一次重算结果吞掉。
        assert (
            client.patch(
                f"/api/v1/trades/{trade.id}",
                json={
                    "revision": 2,
                    "memo": "重算后的新更正",
                },
            ).json()["needsRefresh"]
            is True
        )


def test_pending_closed_scope_survives_new_session_and_gets_do_not_write(context: Context) -> None:
    """已结清且被隐藏的个股仍提供入口；状态从新会话读取且 GET 没有写副作用。"""
    session, account, security, strategy = context
    trade = buy(context)
    sell(context, qty="10")
    with client_for(session) as client:
        before = client.get(scope_url(context, "lots")).json()
        assert before["lots"] == [] and before["needsRefresh"] is False
        assert session.scalar(select(func.count()).select_from(PositionRefreshState)) == 0
        assert (
            client.patch(
                f"/api/v1/trades/{trade.id}",
                json={
                    "revision": 1,
                    "memo": "闭仓后纠错",
                },
            ).status_code
            == 200
        )
        baseline = audit_count(session)
        result = client.get(f"/api/v1/dashboard?accountId={account.id}&mode=cache")
        assert result.status_code == 200, result.text
        dashboard = result.json()
        assert dashboard["positions"] == []
        assert dashboard["pendingRefreshScopes"] == [
            {
                "accountId": account.id,
                "securityId": security.id,
                "strategyId": strategy.id,
                "market": "SH",
                "symbol": "600519",
                "name": "贵州茅台",
            }
        ]
        lots = client.get(scope_url(context, "lots")).json()
        assert lots["accountId"] == account.id and lots["strategyId"] == strategy.id
        assert lots["needsRefresh"] is True
        assert audit_count(session) == baseline
    fresh_engine = create_engine_for_url(str(session.get_bind().url))
    with Session(fresh_engine) as fresh:
        assert PositionRefreshService.needs_refresh(
            fresh, account_id=account.id, security_id=security.id, strategy_id=strategy.id
        )
    fresh_engine.dispose()


def test_active_dashboard_row_exposes_persistent_flag(context: Context) -> None:
    """活动持仓行显示与顶层待刷新清单一致的状态，缓存读取不清标记。"""
    session, account, _, _ = context
    trade = buy(context)
    with client_for(session) as client:
        assert (
            client.patch(
                f"/api/v1/trades/{trade.id}",
                json={
                    "revision": 1,
                    "price": "12",
                },
            ).status_code
            == 200
        )
        result = client.get(f"/api/v1/dashboard?accountId={account.id}&mode=cache")
        assert result.status_code == 200, result.text
        assert result.json()["positions"][0]["needsRefresh"] is True
        assert client.get(scope_url(context, "lots")).json()["needsRefresh"] is True


def test_edit_transaction_rolls_back_trade_audit_and_mark(context: Context, monkeypatch) -> None:
    """在事实已改变后注入标记写入异常，整个请求必须回滚而不残留编辑审计。"""
    session, _, _, _ = context
    trade = buy(context)
    baseline = audit_count(session)

    def fail_mark(*args, **kwargs) -> None:
        """模拟持久标记写入阶段的失败，检验真正的事务边界。"""
        raise RuntimeError("模拟状态写入失败")

    monkeypatch.setattr(PositionRefreshService, "mark", fail_mark)
    with client_for(session, raise_server_exceptions=False) as client:
        result = client.patch(f"/api/v1/trades/{trade.id}", json={"revision": 1, "price": "12"})
        assert result.status_code == 500
    session.refresh(trade)
    assert trade.price == Decimal("10") and trade.revision == 1
    assert audit_count(session) == baseline
    assert session.scalar(select(func.count()).select_from(PositionRefreshState)) == 0


def test_memo_only_edit_retains_subsecond_time_and_every_fee(context: Context) -> None:
    """仅改备注不重建时间、不清零未提交费用，保留微秒及原始字段精度。"""
    session, _, _, _ = context
    trade = buy(context)
    with client_for(session) as client:
        seeded = client.patch(
            f"/api/v1/trades/{trade.id}",
            json={
                "revision": 1,
                "executedAt": "2026-11-01T01:00:00.123456Z",
                "commission": "1.1111",
                "stampDuty": "2.2222",
                "transferFee": "3.3333",
                "otherFee": "4.4444",
            },
        )
        assert seeded.status_code == 200
        before = seeded.json()["trade"]
        assert client.post(scope_url(context, "recalculate")).status_code == 200
        edited = client.patch(
            f"/api/v1/trades/{trade.id}",
            json={
                "revision": 2,
                "memo": "只改备注",
            },
        )
        assert edited.status_code == 200
        after = edited.json()["trade"]
        for name in (
            "executedAt",
            "qty",
            "price",
            "commission",
            "stampDuty",
            "transferFee",
            "otherFee",
            "grossAmount",
            "feesTotal",
        ):
            assert after[name] == before[name]
        assert edited.json()["needsRefresh"] is True


def test_quantity_and_time_edit_rebuilds_remaining_position(context: Context) -> None:
    """合法数量及时间更正经主动重算后更新剩余数量、批次时间与周期。"""
    session, _, _, _ = context
    trade = buy(context)
    sell(context)
    with client_for(session) as client:
        assert (
            client.patch(
                f"/api/v1/trades/{trade.id}",
                json={
                    "revision": 1,
                    "qty": "12",
                    "executedAt": "2026-11-01T02:00:00Z",
                },
            ).status_code
            == 200
        )
        assert client.post(scope_url(context, "recalculate")).status_code == 200
        row = client.get(scope_url(context, "lots")).json()["lots"][0]
        assert row["remainingQty"] == "6.0000"
        assert row["remainingCost"] == "60.0000"
        assert datetime.fromisoformat(row["executedAt"]).hour == 2


def test_repeated_mark_clear_and_failure_after_clear_are_atomic(
    context: Context, monkeypatch
) -> None:
    """重复操作只保留一行；即使末端已清标记再异常，也必须恢复旧派生与标记。"""
    session, account, security, strategy = context
    scope = {"account_id": account.id, "security_id": security.id, "strategy_id": strategy.id}
    PositionRefreshService.clear(session, **scope)
    assert session.scalar(select(func.count()).select_from(PositionRefreshState)) == 0
    PositionRefreshService.mark(session, **scope)
    PositionRefreshService.mark(session, **scope)
    session.commit()
    assert session.scalar(select(func.count()).select_from(PositionRefreshState)) == 1
    PositionRefreshService.clear(session, **scope)
    PositionRefreshService.clear(session, **scope)
    session.commit()
    assert not PositionRefreshService.needs_refresh(session, **scope)
    trade = buy(context)
    sell(context)
    original = projection(session)
    original_clear = PositionRefreshService.clear

    def fail_after_clear(*args, **kwargs) -> None:
        """先实际清除并 flush，再制造提交前异常以验证回滚覆盖最后一步。"""
        original_clear(*args, **kwargs)
        session.flush()
        raise RuntimeError("模拟重算末端故障")

    with client_for(session, raise_server_exceptions=False) as client:
        assert (
            client.patch(
                f"/api/v1/trades/{trade.id}",
                json={
                    "revision": 1,
                    "price": "12",
                },
            ).status_code
            == 200
        )
        baseline = audit_count(session)
        monkeypatch.setattr(PositionRefreshService, "clear", fail_after_clear)
        result = client.post(scope_url(context, "recalculate"))
        assert result.status_code == 500
        session.expire_all()
        assert projection(session) == original
        assert PositionRefreshService.needs_refresh(session, **scope)
        assert audit_count(session) == baseline
        assert session.get(Trade, trade.id).revision == 2
