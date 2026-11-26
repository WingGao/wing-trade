"""关注列表迁移 0008、独立 CRUD API、聚合指标与重买保留集成测试（隔离临时库）。"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_current_user, get_db
from app.domain.enums import UserRole, UserStatus
from app.main import create_app
from app.models import Account, Base, QuoteCache, Security, Strategy, User
from app.services.ledger import TradeLedgerService


def test_watchlist_migration_0008_upgrade_and_downgrade(tmp_path: Path) -> None:
    """验证 0008 迁移无损新增 watchlist_items 字段与 buyback_alert_deliveries 表并可逆回退。"""
    database = tmp_path / "watchlist-migration.sqlite"
    backend = Path(__file__).resolve().parents[2]
    environment = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{database.as_posix()}",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }

    def migrate(target: str, *, downgrade: bool = False) -> None:
        """在独立临时库运行 Alembic 迁移，严禁触碰根目录 ledger.db。"""
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade" if downgrade else "upgrade", target],
            cwd=backend,
            env=environment,
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    migrate("0007_security_daily_metrics")
    with sqlite3.connect(database) as conn:
        conn.execute(
            "INSERT INTO accounts (id, name, currency, created_at) VALUES (?, ?, ?, ?)",
            ("acc-1", "A股账户", "CNY", "2026-11-25 00:00:00"),
        )
        conn.execute(
            "INSERT INTO securities (id, market, symbol, name, security_type, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("sec-1", "SH", "600519", "贵州茅台", "STOCK", "ACTIVE", "2026-11-25 00:00:00"),
        )
        conn.execute(
            "INSERT INTO watchlist_items (id, account_id, security_id, sort_order, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("wl-1", "acc-1", "sec-1", 0, "2026-11-25 00:00:00"),
        )

    migrate("head")
    with sqlite3.connect(database) as conn:
        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(watchlist_items)").fetchall()
        }
        assert {"target_buy_price", "alert_enabled", "note", "updated_at"}.issubset(cols)
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "buyback_alert_deliveries" in tables
        row = conn.execute(
            "SELECT id, alert_enabled, updated_at FROM watchlist_items WHERE id='wl-1'"
        ).fetchone()
        assert row is not None
        assert row[0] == "wl-1"
        assert row[1] == 0
        assert row[2] is not None

    migrate("0007_security_daily_metrics", downgrade=True)
    with sqlite3.connect(database) as conn:
        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(watchlist_items)").fetchall()
        }
        assert "target_buy_price" not in cols
        assert "alert_enabled" not in cols
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "buyback_alert_deliveries" not in tables


def _make_client(
    tmp_path: Path,
) -> tuple[TestClient, sessionmaker[Session], dict[str, User], dict[str, str]]:
    """构造隔离 SQLite 与两位用户的 FastAPI 测试客户端。"""
    db_file = tmp_path / "watchlist-api.sqlite"
    engine = create_engine(
        f"sqlite:///{db_file.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with session_factory() as session:
        user_a = User(
            email="wa@example.com",
            display_name="用户A",
            role=UserRole.ADMIN.value,
            status=UserStatus.ACTIVE.value,
            is_default_admin=True,
        )
        user_b = User(
            email="wb@example.com",
            display_name="用户B",
            role=UserRole.USER.value,
            status=UserStatus.ACTIVE.value,
            is_default_admin=False,
        )
        session.add_all([user_a, user_b])
        session.flush()
        acc_a = Account(user_id=user_a.id, name="A股主账户", currency="CNY")
        acc_b = Account(user_id=user_b.id, name="用户B账户", currency="CNY")
        session.add_all([acc_a, acc_b])
        session.flush()
        strat_a = Strategy(account_id=acc_a.id, name="默认策略")
        strat_b = Strategy(account_id=acc_b.id, name="默认策略")
        sec = Security(market="SH", symbol="600519", name="贵州茅台", security_type="STOCK")
        session.add_all([strat_a, strat_b, sec])
        session.flush()
        session.add(
            QuoteCache(
                security_id=sec.id,
                last_price=Decimal("1400.000000"),
                prev_close=Decimal("1420.000000"),
                change_pct=Decimal("-0.014085"),
                source_time=datetime(2026, 11, 26, 2, 30, tzinfo=UTC),
                fetched_at=datetime(2026, 11, 26, 2, 30, tzinfo=UTC),
                provider="mock",
                is_stale=False,
            )
        )
        session.commit()
        ids = {
            "acc_a": acc_a.id,
            "acc_b": acc_b.id,
            "strat_a": strat_a.id,
            "sec_id": sec.id,
        }

    active_user: dict[str, User] = {"current": user_a, "user_a": user_a, "user_b": user_b}
    app = create_app()

    def override_db() -> Generator[Session, None, None]:
        """提供隔离临时库会话。"""
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: active_user["current"]
    return TestClient(app), session_factory, active_user, ids


def test_watchlist_crud_validation_enrichment_and_rebuy_retention(tmp_path: Path) -> None:
    """验证关注增改删校验、清仓聚合字段计算、多用户隔离与重买后保留关注状态。"""
    client, session_factory, active_user, ids = _make_client(tmp_path)
    acc_a = ids["acc_a"]
    sec_id = ids["sec_id"]
    strat_a = ids["strat_a"]

    # 先模拟一笔买入 + 全部清仓卖出（买入 1300，卖出 1500）
    with session_factory() as session:
        ledger = TradeLedgerService(session)
        ledger.buy(
            account_id=acc_a,
            security_id=sec_id,
            strategy_id=strat_a,
            qty=Decimal("100"),
            price=Decimal("1300"),
            executed_at=datetime(2026, 11, 20, 2, 0, tzinfo=UTC),
            commission=Decimal("0"),
            other_fee=Decimal("0"),
        )
        ledger.sell(
            account_id=acc_a,
            security_id=sec_id,
            strategy_id=strat_a,
            qty=Decimal("100"),
            price=Decimal("1500"),
            executed_at=datetime(2026, 11, 24, 6, 0, tzinfo=UTC),
            commission=Decimal("0"),
            other_fee=Decimal("0"),
        )

    # 1. 开启提醒但未填目标买回价应被拒绝
    bad_resp = client.post(
        f"/api/v1/watchlist?accountId={acc_a}",
        json={"securityId": sec_id, "alertEnabled": True},
    )
    assert bad_resp.status_code == 422

    # 2. 目标买回价 <= 0 应被拒绝
    bad_price_resp = client.post(
        f"/api/v1/watchlist?accountId={acc_a}",
        json={"securityId": sec_id, "targetBuyPrice": "0", "alertEnabled": True},
    )
    assert bad_price_resp.status_code == 422

    # 3. 正常加入关注（当前价 1400 <= 目标买回价 1420，应命中 hitBuyTarget）
    create_resp = client.post(
        f"/api/v1/watchlist?accountId={acc_a}",
        json={
            "securityId": sec_id,
            "targetBuyPrice": "1420.00",
            "alertEnabled": True,
            "note": "跌回 1420 分批接回",
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    created = create_resp.json()
    assert created["securityId"] == sec_id
    assert created["hasOpenPosition"] is False
    assert created["alertEnabled"] is True
    assert created["note"] == "跌回 1420 分批接回"
    assert created["hitBuyTarget"] is True
    assert Decimal(created["lastSellPrice"]) == Decimal("1500")
    assert Decimal(created["realizedPnl"]) == Decimal("20000")
    assert Decimal(created["vsLastSellPct"]) < 0
    assert Decimal(created["vsTargetBuyPct"]) < 0

    # 4. PATCH 更新目标买回价为 1350（当前价 1400 > 1350 -> hitBuyTarget 为 False）
    patch_resp = client.patch(
        f"/api/v1/watchlist/{sec_id}?accountId={acc_a}",
        json={"targetBuyPrice": "1350.00", "note": "等更低位置"},
    )
    assert patch_resp.status_code == 200, patch_resp.text
    patched = patch_resp.json()
    assert Decimal(patched["targetBuyPrice"]) == Decimal("1350")
    assert patched["alertEnabled"] is True
    assert patched["hitBuyTarget"] is False
    assert patched["note"] == "等更低位置"

    # 5. 重新买入后保留关注状态，且 hasOpenPosition 变为 True
    with session_factory() as session:
        TradeLedgerService(session).buy(
            account_id=acc_a,
            security_id=sec_id,
            strategy_id=strat_a,
            qty=Decimal("50"),
            price=Decimal("1380"),
            executed_at=datetime(2026, 11, 26, 2, 0, tzinfo=UTC),
            commission=Decimal("0"),
            other_fee=Decimal("0"),
        )

    list_resp = client.get(f"/api/v1/watchlist?accountId={acc_a}")
    assert list_resp.status_code == 200
    items = list_resp.json()
    assert len(items) == 1
    assert items[0]["hasOpenPosition"] is True
    assert items[0]["alertEnabled"] is True
    assert Decimal(items[0]["targetBuyPrice"]) == Decimal("1350")

    # 6. 清空目标买回价时自动关闭提醒开关
    clear_resp = client.patch(
        f"/api/v1/watchlist/{sec_id}?accountId={acc_a}",
        json={"targetBuyPrice": None},
    )
    assert clear_resp.status_code == 200
    assert clear_resp.json()["targetBuyPrice"] is None
    assert clear_resp.json()["alertEnabled"] is False

    # 7. 用户隔离：用户 B 看不到用户 A 的关注记录
    active_user["current"] = active_user["user_b"]
    b_list = client.get(f"/api/v1/watchlist?accountId={ids['acc_b']}")
    assert b_list.status_code == 200
    assert b_list.json() == []

    # 8. 切回用户 A 删除关注
    active_user["current"] = active_user["user_a"]
    del_resp = client.delete(f"/api/v1/watchlist/{sec_id}?accountId={acc_a}")
    assert del_resp.status_code == 204
    assert client.get(f"/api/v1/watchlist?accountId={acc_a}").json() == []
